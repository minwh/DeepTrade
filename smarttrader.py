# -*- coding: utf-8 -*-
# Copyright 2017 The Xiaoyu Fang. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

import tensorflow as tf
from tensorflow.contrib import rnn
import os

from tensorflow.contrib.rnn import DropoutWrapper
from tensorflow.python.ops.init_ops import glorot_uniform_initializer, orthogonal_initializer
from rawdata import RawData, read_sample_data
from dataset import DataSet
from chart import extract_feature
import numpy
from tensorflow.contrib.layers.python.layers.layers import batch_norm
import sys
from numpy.random import seed


class SmartTrader(object):
    def __init__(self, step, input_size, starter_learning_rate, hidden_size, nclasses, decay_step=500, decay_rate=1.0, cost=0.0002):
        '''
        Initialize parameters for the SmartTrader
        :param step: time steps of the feature
        :param input_size: size of each time step of the feature
        :param starter_learning_rate: initial learning rate, the learning rate decays along global train step
        :param hidden_size: hidden units of the LSTM layer
        :param nclasses: number of classes, should be 1
        :param decay_step: learning rate decay step
        :param decay_rate: learning rate decay rate
        :param cost: the constant cost for money occupied by buying stock
        '''
        self.step = step
        self.input_size = input_size
        self.global_step = None
        self.starter_learning_rate = starter_learning_rate
        self.decay_step = decay_step
        self.decay_rate = decay_rate
        self.learning_rate = None
        self.hidden_size = hidden_size
        self.nclasses = nclasses
        self.position = None
        self.summary_op = None
        self.weights = None
        self.biases = None
        self.cost = cost
        self.loss = None
        self.avg_position = None
        self.keep_rate = None
        self.x = None
        self.y = None
        self.is_training = None

    def _create_learning_rate(self):
        '''
        create learning rate
        :return:
        '''
        with tf.variable_scope("parameter"):
            self.global_step = tf.Variable(0, trainable=False, name="global_step")
            self.learning_rate = tf.train.exponential_decay(self.starter_learning_rate, self.global_step,
                                                   self.decay_step, self.decay_rate, staircase=True, name="learning_rate")

    def _create_placeholders(self):
        with tf.variable_scope("input"):
            self.x = tf.placeholder(tf.float32, shape=[None, self.step, self.input_size], name='history_feature')
            self.y = tf.placeholder(tf.float32, shape=[None, 1], name='target')
            self.is_training = tf.placeholder(tf.bool, name='mode')
            self.keep_rate = tf.placeholder(tf.float32, name='kepp_rate')

    def _create_weights(self):
        with tf.variable_scope("weights"):
            self.weights = {
                'out': tf.get_variable("weights", [self.hidden_size, self.nclasses],
                                       initializer=tf.random_normal_initializer(mean=0, stddev=0.01, seed=1))
            }
            self.biases = {
                'out': tf.get_variable("bias", [self.nclasses], initializer=tf.random_normal_initializer(mean=0, stddev=0.01, seed=1))
            }

    def batch_norm_layer(self, signal, scope):
        '''
        batch normalization layer before activation
        :param signal: input signal
        :param scope: name scope
        :return: normalized signal
        '''
        # Note: is_training is tf.placeholder(tf.bool) type
        return tf.cond(self.is_training,
                       lambda: batch_norm(signal, is_training=True, param_initializers={"beta": tf.constant_initializer(3.), "gamma": tf.constant_initializer(2.5)},
                                          center=True, scale=True, activation_fn=tf.nn.relu, decay=1., scope=scope),
                       lambda: batch_norm(signal, is_training=False, param_initializers={"beta": tf.constant_initializer(3.), "gamma": tf.constant_initializer(2.5)},
                                          center=True, scale=True, activation_fn=tf.nn.relu, decay=1.,
                                          scope=scope, reuse=True))

    def _create_loss(self):
        '''
        Risk estimation loss function. The output is the planed position we should hold to next day. The change rate of
        next day is self.y, so we loss two categories of money: - self.y * self.position is trade loss,
        cost * self.position is constant loss because of tax and like missing profit of buying national debt. Therefore,
        the loss function is formulated as: 100 * (- self.y * self.position + cost * self.position) = -100 * ((self.y - cost) * self.position)
        :return:
        '''
        #with tf.device("/cpu:0"):
        xx = tf.unstack(self.x, self.step, 1)
        lstm_cell = rnn.LSTMCell(self.hidden_size, forget_bias=1.0, initializer=orthogonal_initializer())
        dropout_cell = DropoutWrapper(lstm_cell, input_keep_prob=self.keep_rate, output_keep_prob=self.keep_rate, state_keep_prob=self.keep_rate)
        outputs, states = rnn.static_rnn(dropout_cell, xx, dtype=tf.float32)
        signal = tf.matmul(outputs[-1], self.weights['out']) + self.biases['out']
        scope = "activation_batch_norm"
        norm_signal = self.batch_norm_layer(signal, scope=scope)
        # batch_norm(signal, 0.9, center=True, scale=True, epsilon=0.001, activation_fn=tf.nn.relu6,
        #           is_training=is_training, scope="activation_batch_norm", reuse=False)
        self.position = tf.nn.relu6(norm_signal, name="relu_limit") / 6.
        self.avg_position = tf.reduce_mean(self.position)
        # self.cost = 0.0002
        self.loss = -100. * tf.reduce_mean(tf.multiply((self.y - self.cost), self.position, name="estimated_risk"))

    def _create_optimizer(self):
        '''
        create optimizer
        :return:
        '''
        #with tf.device("/cpu:0"):
        self.optimizer = tf.train.RMSPropOptimizer(self.learning_rate, name="optimizer").minimize(self.loss, global_step=self.global_step)

    def _create_summary(self):
        tf.summary.scalar("loss", self.loss)
        tf.summary.histogram("histogram loss", self.loss)
        tf.summary.scalar("average position", self.avg_position)
        tf.summary.histogram("histogram position", self.avg_position)
        self.summary_op = tf.summary.merge_all()

    def build_graph(self):
        self._create_learning_rate()
        self._create_placeholders()
        self._create_weights()
        self._create_loss()
        self._create_optimizer()
        self._create_summary()


def train(trader, train_set, val_set, train_steps=10000, batch_size=32, keep_rate=1.):
    initial_step = 1
    val_features = val_set.images
    val_labels = val_set.labels
    VERBOSE_STEP = 10  # int(len(train_features) / batch_size)
    VALIDATION_STEP = VERBOSE_STEP * 100

    saver = tf.train.Saver()
    min_validation_loss = 100000000.
    with tf.Session() as sess:
        sess.run(tf.global_variables_initializer())
        writer = tf.summary.FileWriter("./graphs", sess.graph)
        for i in range(initial_step, initial_step + train_steps):
            batch_features, batch_labels = train_set.next_batch(batch_size)
            _, loss, avg_pos, summary = sess.run([trader.optimizer, trader.loss, trader.avg_position, trader.summary_op],
                                        feed_dict={trader.x: batch_features, trader.y: batch_labels,
                                                   trader.is_training: True, trader.keep_rate: keep_rate})
            writer.add_summary(summary, global_step=i)
            if i % VERBOSE_STEP == 0:
                hint = None
                if i % VALIDATION_STEP == 0:
                    val_loss, val_avg_pos = sess.run([trader.loss, trader.avg_position],
                                           feed_dict={trader.x: val_features, trader.y: val_labels,
                                           trader.is_training: False, trader.keep_rate: 1.})
                    hint = 'Average Train Loss at step {}: {:.7f} Average position {:.7f}, Validation Loss: {:.7f} Average Position: {:.7f}'.format(i, loss, avg_pos, val_loss, val_avg_pos)
                    if val_loss < min_validation_loss:
                        min_validation_loss = val_loss
                        saver.save(sess, "./checkpoint/best_model", i)
                else:
                    hint = 'Average loss at step {}: {:.7f} Average position {:.7f}'.format(i, loss, avg_pos)
                print(hint)


def calculate_cumulative_return(labels, pred):
    cr = []
    if len(labels) <= 0:
        return cr
    cr.append(1. * (1. + labels[0] * pred[0]))
    for l in range(1, len(labels)):
        cr.append(cr[l - 1] * (1 + labels[l] * pred[l]))
    for i in range(len(cr)):
        cr[i] = cr[i] - 1
    return cr


def predict(val_set, step=30, input_size=61, learning_rate=0.001, hidden_size=8, nclasses=1):
    features = val_set.images
    labels = val_set.labels
    trader = SmartTrader(step, input_size, learning_rate, hidden_size, nclasses)
    trader.build_graph()
    saver = tf.train.Saver()
    with tf.Session() as sess:
        sess.run(tf.global_variables_initializer())
        ckpt = tf.train.get_checkpoint_state(os.path.dirname('checkpoint/checkpoint'))
        if ckpt and ckpt.model_checkpoint_path:
            saver.restore(sess, ckpt.model_checkpoint_path)
        pred, avg_pos = sess.run([trader.position, trader.avg_position],
                                 feed_dict={trader.x: features, trader.y: labels,
                                            trader.is_training: False, trader.keep_rate: 1.})

        cr = calculate_cumulative_return(labels, pred)
        print("changeRate\tpositionAdvice\tprincipal\tcumulativeReturn")
        for i in range(len(labels)):
            print(str(labels[i]) + "\t" + str(pred[i]) + "\t" + str(cr[i] + 1.) + "\t" + str(cr[i]))
        #print("ChangeRate\tPositionAdvice")
        #for i in range(len(labels)):
        #    print(str(labels[i][0]) + "\t" + str(pred[i][0]))


def main(operation='train', code=None):
    step = 30
    #input_size = 61
    input_size = 41
    train_steps = 1000000
    batch_size = 512
    learning_rate = 0.001
    hidden_size = 14
    nclasses = 1
    validation_size = 700
    #validation_size = 240 * 30
    keep_rate = 0.7

    selector = ["ROCP", "OROCP", "HROCP", "LROCP", "MACD", "RSI", "VROCP", "BOLL", "MA", "VMA", "PRICE_VOLUME"]
    input_shape = [30, input_size]  # [length of time series, length of feature]

    if operation == 'train':
        dataset_dir = "./dataset"
        train_features = []
        train_labels = []
        val_features = []
        val_labels = []
        for filename in os.listdir(dataset_dir):
            if code is not None and filename != code + '.csv':
                continue
            #if filename != '000001.csv':
            #    continue
            print("processing file: " + filename)
            filepath = dataset_dir + "/" + filename
            raw_data = read_sample_data(filepath)
            moving_features, moving_labels = extract_feature(raw_data=raw_data, selector=selector, window=input_shape[0],
                                                             with_label=True, flatten=False)
            train_features.extend(moving_features[:-validation_size])
            train_labels.extend(moving_labels[:-validation_size])
            val_features.extend(moving_features[-validation_size:])
            val_labels.extend(moving_labels[-validation_size:])

        train_features = numpy.transpose(numpy.asarray(train_features), [0, 2, 1])
        train_labels = numpy.asarray(train_labels)
        train_labels = numpy.reshape(train_labels, [train_labels.shape[0], 1])
        val_features = numpy.transpose(numpy.asarray(val_features), [0, 2, 1])
        val_labels = numpy.asarray(val_labels)
        val_labels = numpy.reshape(val_labels, [val_labels.shape[0], 1])
        train_set = DataSet(train_features, train_labels)
        val_set = DataSet(val_features, val_labels)

        # raw_data = read_sample_data("toy_stock.csv")
        # moving_features, moving_labels = extract_feature(raw_data=raw_data, selector=selector, window=input_shape[0],
        #                                                 with_label=True, flatten=False)
        # moving_features = numpy.asarray(moving_features)
        # moving_features = numpy.transpose(moving_features, [0, 2, 1])
        # moving_labels = numpy.asarray(moving_labels)
        # moving_labels = numpy.reshape(moving_labels, [moving_labels.shape[0], 1])
        # train_set = DataSet(moving_features[:-validation_size], moving_labels[:-validation_size])
        # val_set = DataSet(moving_features[-validation_size:], moving_labels[-validation_size:])

        trader = SmartTrader(step, input_size, learning_rate, hidden_size, nclasses)
        trader.build_graph()
        train(trader, train_set, val_set, train_steps, batch_size=batch_size, keep_rate=keep_rate)
    elif operation == "predict":
        predict_file_path = "./dataset/000001.csv"
        if code is not None:
            predict_file_path = "./dataset/%s.csv" % code
        print("processing file %s" % predict_file_path)
        raw_data = read_sample_data(predict_file_path)
        moving_features, moving_labels = extract_feature(raw_data=raw_data, selector=selector, window=input_shape[0],
                                                         with_label=True, flatten=False)
        moving_features = numpy.asarray(moving_features)
        moving_features = numpy.transpose(moving_features, [0, 2, 1])
        moving_labels = numpy.asarray(moving_labels)
        moving_labels = numpy.reshape(moving_labels, [moving_labels.shape[0], 1])
        # train_set = DataSet(moving_features[:-validation_size], moving_labels[:-validation_size])
        val_set = DataSet(moving_features[-validation_size:], moving_labels[-validation_size:])
        predict(val_set, step=step, input_size=input_size, learning_rate=learning_rate, hidden_size=hidden_size, nclasses=nclasses)

    else:
        print("Operation not supported. ")

if __name__ == '__main__':
    tf.set_random_seed(2)
    seed(1)
    operation = 'train'
    code = None
    if len(sys.argv) > 1:
        operation = sys.argv[1]
    if len(sys.argv) > 2:
        code = sys.argv[2]
    main(operation, code)
