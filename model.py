import tensorflow as tf
from ops import *
from utils import utils
import numpy as np
import os
import sys
from flags import BOS, EOS, UNK, DROPOUT



class vrnn():
    
    def __init__(self,args,sess):
        self.sess = sess
        self.word_embedding_dim = 300
        self.num_steps = args.num_steps
        self.latent_dim = args.latent_dim
        self.sequence_length = args.sequence_length
        self.output = args.output
        self.batch_size = args.batch_size
        self.saving_step = args.saving_step
        self.printing_step = args.printing_step
        self.feed_previous = args.feed_previous
        self.model_dir = args.model_dir
        self.data_dir = args.data_dir
        self.load = args.load
        self.lstm_length = [self.sequence_length+1]*self.batch_size
        self.utils = utils(args)
        self.vocab_size = len(self.utils.word_id_dict)
        self.word_dp = args.word_dp
        if args.mode == 'train':
          self.KL_annealing = args.kl
        else:
          self.KL_annealing = False

        self.BOS = BOS
        self.EOS = EOS
        self.log_dir = os.path.join(self.model_dir,'log/')
        self.build_graph()
        
        self.saver = tf.train.Saver(max_to_keep=5)
        self.model_path = os.path.join(self.model_dir,'model_{m_type}'.format(m_type='vrnn'))

    def build_graph(self):
        print('starting building graph')
        
        with tf.variable_scope("input") as scope:
            self.encoder_inputs = tf.placeholder(dtype=tf.int32, shape=(self.batch_size, self.sequence_length))
            self.train_decoder_sentence = tf.placeholder(dtype=tf.int32, shape=(self.batch_size, self.sequence_length))
            self.train_decoder_targets = tf.placeholder(dtype=tf.int32, shape=(self.batch_size, self.sequence_length))
            self.step = tf.placeholder(dtype=tf.float32, shape=())

            BOS_slice = tf.ones([self.batch_size, 1], dtype=tf.int32)*self.BOS
            EOS_slice = tf.ones([self.batch_size, 1], dtype=tf.int32)*self.EOS
            train_decoder_targets = tf.concat([self.train_decoder_targets,EOS_slice],axis=1)            
            train_decoder_sentence = tf.concat([BOS_slice,self.train_decoder_sentence],axis=1)
          
    
        with tf.variable_scope("embedding") as scope:
            self.embedding_placeholder = tf.placeholder(dtype=tf.float32, shape=(self.vocab_size-4,self.word_embedding_dim))
            init = tf.contrib.layers.xavier_initializer()


            word_vector_BOS_EOS = tf.get_variable(
                name="word_vector_BOS_EOS",
                shape=[2, self.word_embedding_dim],
                initializer = init,
                trainable = True)

            word_vector_UNK_DROPOUT = tf.get_variable(
                name="word_vector_UNK_DROPOUT",
                shape=[2, self.word_embedding_dim],
                initializer = init,
                trainable = True)

            pretrained_word_embd  = tf.get_variable(
                name="pretrained_word_embd",
                shape=[self.vocab_size-4, self.word_embedding_dim],
                initializer = init,
                trainable = False)
            self.embd_init = pretrained_word_embd.assign(self.embedding_placeholder)

            # word embedding
            word_embedding_matrix = tf.concat([word_vector_BOS_EOS, word_vector_UNK_DROPOUT, pretrained_word_embd], 0)

            # decoder output projection
            weight_output = tf.get_variable(
                name="weight_output",
                shape=[self.latent_dim*2, self.vocab_size],
                initializer =  init,
                trainable = True)

            bias_output = tf.get_variable(
                name="bias_output",
                shape=[self.vocab_size],
                initializer = tf.constant_initializer(value = 0.0),
                trainable = True)
    
            encoder_inputs_embedded = tf.nn.embedding_lookup(word_embedding_matrix, self.encoder_inputs)
    
        with tf.variable_scope("encoder") as scope:
            cell_fw = tf.contrib.rnn.LSTMCell(num_units=self.latent_dim, state_is_tuple=True)
            cell_bw = tf.contrib.rnn.LSTMCell(num_units=self.latent_dim, state_is_tuple=True)
            #bi-lstm encoder
            encoder_outputs, state = tf.nn.bidirectional_dynamic_rnn(
                cell_fw=cell_fw,
                cell_bw=cell_bw,
                dtype=tf.float32,
                sequence_length=self.lstm_length,
                inputs=encoder_inputs_embedded,
                time_major=False)
    
            output_fw, output_bw = encoder_outputs
            state_fw, state_bw = state
            encoder_outputs = tf.concat([output_fw,output_bw],2)      
            self.encoder_outputs=encoder_outputs
            encoder_state_c = tf.concat((state_fw.c, state_bw.c), 1)
            encoder_state_h = tf.concat((state_fw.h, state_bw.h), 1)
            self.encoder_state_c=encoder_state_c
            self.encoder_state_h=encoder_state_h
        
        with tf.variable_scope("sample") as scope:
        
            w_mean = weight_variable([self.latent_dim*2,self.latent_dim*2],0.1)
            b_mean = bias_variable([self.latent_dim*2])
            scope.reuse_variables()
            b_mean_matrix = [b_mean] * self.batch_size
            
            w_logvar = weight_variable([self.latent_dim*2,self.latent_dim*2],0.1)
            b_logvar = bias_variable([self.latent_dim*2])
            scope.reuse_variables()
            b_logvar_matrix = [b_logvar] * self.batch_size
            
            mean = tf.matmul(encoder_state_h,w_mean) + b_mean
            logvar = tf.matmul(encoder_state_h,w_logvar) + b_logvar
            var = tf.exp( 0.5 * logvar)
            noise = tf.random_normal(tf.shape(var))
            sampled_encoder_state_h = mean + tf.multiply(var,noise)
            
                
        encoder_state = tf.contrib.rnn.LSTMStateTuple(c=encoder_state_c, h=sampled_encoder_state_h) 
        decoder_inputs = batch_to_time_major(train_decoder_sentence, self.sequence_length+1)  
        
        with tf.variable_scope("decoder") as scope:
        
            cell = tf.contrib.rnn.LSTMCell(num_units=self.latent_dim*2, state_is_tuple=True)
            self.cell = cell
                
            #the decoder of training
            train_decoder_output,train_decoder_state = tf.contrib.legacy_seq2seq.embedding_rnn_decoder(
                decoder_inputs = decoder_inputs,
                initial_state = encoder_state,
                cell = cell,
                num_symbols = self.vocab_size,
                embedding_size = self.word_embedding_dim,
                output_projection = (weight_output, bias_output),
                feed_previous = self.feed_previous,
                scope = scope
            )
            
            #the decoder of testing
            scope.reuse_variables()
            test_decoder_output,test_decoder_state = tf.contrib.legacy_seq2seq.embedding_rnn_decoder(
                decoder_inputs = decoder_inputs,
                initial_state = encoder_state,
                cell = cell,
                num_symbols = self.vocab_size,
                embedding_size = self.word_embedding_dim,
                output_projection = (weight_output, bias_output),
                feed_previous = self.feed_previous,
                scope = scope
            )   
            
            for index,time_slice in enumerate(test_decoder_output):
                test_decoder_output[index] = tf.add(tf.matmul(test_decoder_output[index],weight_output),bias_output)
                train_decoder_output[index] = tf.add(tf.matmul(train_decoder_output[index],weight_output),bias_output)
           
            test_decoder_logits = tf.stack(test_decoder_output, axis=1)
            test_pred = tf.argmax(test_decoder_logits,axis=-1)
            test_pred = tf.to_int32(test_pred,name='ToInt32')
    
            self.test_pred=test_pred
                                                           
        
    
        with tf.variable_scope("loss") as scope:
        
        
            kl_loss_batch = tf.reduce_sum( -0.5 * (logvar - tf.square(mean) - tf.exp(logvar) + 1.0) , 1)
            kl_loss = tf.reduce_mean(kl_loss_batch, 0) #mean of kl_cost over batch
            if(self.KL_annealing):
                step_scale = tf.constant(10000, dtype=tf.float32)
                kl_weight = tf.sigmoid(tf.divide(tf.subtract(self.step,step_scale),step_scale ))
                kl_loss = tf.scalar_mul(kl_loss, kl_weight)
            self.kl_loss = kl_loss

            targets = batch_to_time_major(train_decoder_targets,self.sequence_length+1)
            loss_weights = [tf.ones([self.batch_size],dtype=tf.float32) for _ in range(self.sequence_length+1)]    #the weight at each time step
            self.loss = tf.reduce_sum(tf.contrib.legacy_seq2seq.sequence_loss(
                logits = train_decoder_output, 
                targets = targets,
                weights = loss_weights,
                average_across_timesteps = False )) + kl_loss
            #self.train_op = tf.train.RMSPropOptimizer(0.001).minimize(self.loss)
            self.train_op = tf.train.AdamOptimizer(0.0001).minimize(self.loss)
            
            #op_func = tf.train.AdamOptimizer()
            #tvars = tf.trainable_variables()
            #self.gradient = tf.gradients(self.loss, tvars) 
            #capped_grads, _ = tf.clip_by_global_norm(self.gradient, 1)
            #self.train_op = op_func.apply_gradients(zip(capped_grads, tvars))
            
            tf.summary.scalar('total_loss', self.loss)
    
    
    def train(self):
        summary = tf.summary.merge_all()
        summary_writer = tf.summary.FileWriter(self.log_dir, self.sess.graph)
        saving_step = self.saving_step
        summary_step = self.printing_step
        cur_loss = 0.0
        cur_kl_loss = 0.0
        step = 0
       
        ckpt = tf.train.get_checkpoint_state(self.model_dir)
        if ckpt:
            print('load model from:', ckpt.model_checkpoint_path)
            self.saver.restore(self.sess, ckpt.model_checkpoint_path)
            step = int(ckpt.model_checkpoint_path.split('-')[-1])
        else:
            self.sess.run(tf.global_variables_initializer())
            self.sess.run(self.embd_init,{self.embedding_placeholder:self.utils.load_word_embedding()})
        
        for idx, sen in self.utils.train_data_generator():
            step += 1
            t_d = self.utils.word_drop_out(idx, self.word_dp)
            feed_dict = {
                self.encoder_inputs: idx,\
                self.train_decoder_sentence: t_d,\
                self.train_decoder_targets: idx, \
                self.step: step #KL weight
            }
            preds, loss, kl_loss, _ = self.sess.run([self.test_pred, self.loss, self.kl_loss, self.train_op], feed_dict)
            cur_loss += loss
            cur_kl_loss += kl_loss
            if step%(summary_step)==0:
                print('original :')
                print(sen[0])
                print('pred:')
                print(self.utils.id2sent(preds[0]))
                print('\n{step}: total_loss: {loss} kl: {kl_loss}\n'.format(step=step,loss=cur_loss/summary_step, kl_loss=cur_kl_loss/summary_step))
                cur_loss = 0.0
                cut_kl_loss = 0.0
            if step%saving_step==0:
                self.saver.save(self.sess, self.model_path, global_step=step)
            if step>=self.num_steps:
                break  
                
    def stdin_test(self):
        self.saver.restore(self.sess, tf.train.latest_checkpoint(self.model_dir))
        sentence = 'Hi~'
        print(sentence)
        while(sentence):
            print('')
            sentence = sys.stdin.readline()
            sys.stdout.flush()
            input_sent_vec = self.utils.sent2id(sentence, sp=True)
            #print(input_sent_vec)
            sent_vec = np.ones((self.batch_size,self.sequence_length),dtype=np.int32)
            sent_vec[0] = input_sent_vec
            t = np.zeros((self.batch_size,self.sequence_length),dtype=np.int32)
            feed_dict = {
                    self.encoder_inputs:sent_vec,\
                    self.train_decoder_sentence:t
            }
            preds = self.sess.run([self.test_pred],feed_dict)
            pred_sent = self.utils.id2sent(preds[0][0])
            print('->  '+pred_sent)   
            

    def val(self):
        if self.load != '':
          print('load model from {} ... '.format(self.load))
          self.saver.restore(self.sess, self.load)
        else:
          print('load model from {} ... '.format(tf.train.latest_checkpoint(self.model_dir)))
          self.saver.restore(self.sess, tf.train.latest_checkpoint(self.model_dir))        
        step = 0
        cur_loss = 0.0
        cur_kl_loss = 0.0
        f = open(self.output, 'w')
        for s, sen in self.utils.test_data_generator():
            step += 1
            t = np.zeros((self.batch_size,self.sequence_length), dtype=np.int32)
            t_d = s
            feed_dict = {
                self.encoder_inputs: s,\
                self.train_decoder_targets: t_d,\
                self.train_decoder_sentence: t
            }
            preds, loss, kl_loss = self.sess.run([self.test_pred, self.loss, self.kl_loss], feed_dict)
            cur_loss += loss
            cur_kl_loss += kl_loss
            for i in range(self.batch_size):
              #print("{} | {}".format(''.join(sen[i].split()), self.utils.id2sent(preds[i])))
              f.write("{} | {}\n".format(''.join(sen[i].split()), self.utils.id2sent(preds[i])))
        f.write('Total Loss: {}\n'.format(cur_loss/step))
        f.write('KL Divergence: {}\n'.format(cur_kl_loss/step))
           
        print('total loss: ' + str(cur_loss/step))
        print('kl divergence: ' + str(cur_kl_loss/step))

    def get_var_list(self):
            return tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='decoder') + tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='embedding')

