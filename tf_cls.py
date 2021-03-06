import tensorflow as tf
from model_cls import pointnet2
import matplotlib

from pointnet2_cls_msg import placeholder_inputs

matplotlib.use('AGG')
import matplotlib.pyplot as plt
import os
from keras import backend as K
from modelnet_h5_dataset import ModelNetH5Dataset
import numpy as np

nb_classes = 40

epochs = 150
batch_size = 16
num_point = 1024

decay_step = 200000
bn_init_decay = 0.5
bn_decay_decay_rate = 0.5
bn_decay_decay_step = float(decay_step)
bn_decay_clip = 0.99
decay_rate = 0.7

summary_dir = 'summary'


def plot_history(history, result_dir):
    plt.plot(history.history['acc'], marker='.')
    plt.plot(history.history['val_acc'], marker='.')
    plt.title('model accuracy')
    plt.xlabel('epoch')
    plt.ylabel('accuracy')
    plt.grid()
    plt.legend(['acc', 'val_acc'], loc='lower right')
    plt.savefig(os.path.join(result_dir, 'model_accuracy.png'))
    plt.close()

    plt.plot(history.history['loss'], marker='.')
    plt.plot(history.history['val_loss'], marker='.')
    plt.title('model loss')
    plt.xlabel('epoch')
    plt.ylabel('loss')
    plt.grid()
    plt.legend(['loss', 'val_loss'], loc='upper right')
    plt.savefig(os.path.join(result_dir, 'model_loss.png'))
    plt.close()


def save_history(history, result_dir):
    loss = history.history['loss']
    acc = history.history['acc']
    val_loss = history.history['val_loss']
    val_acc = history.history['val_acc']
    nb_epoch = len(acc)

    with open(os.path.join(result_dir, 'result.txt'), 'w') as fp:
        fp.write('epoch\tloss\tacc\tval_loss\tval_acc\n')
        for i in range(nb_epoch):
            fp.write('{}\t{}\t{}\t{}\t{}\n'.format(
                i, loss[i], acc[i], val_loss[i], val_acc[i]))
        fp.close()


def get_learning_rate(batch):
    learning_rate = tf.train.exponential_decay(0.001,  # Base learning rate.
                                               batch * batch_size,  # Current index into the dataset.
                                               decay_step,  # Decay step.
                                               decay_rate,  # Decay rate.
                                               staircase=True)
    learning_rate = tf.maximum(learning_rate, 0.00001)  # CLIP THE LEARNING RATE!
    return learning_rate


def get_bn_decay(batch):
    bn_momentum = tf.train.exponential_decay(bn_init_decay,
                                             batch * batch_size,
                                             bn_decay_decay_step,
                                             bn_decay_decay_rate,
                                             staircase=True)
    bn_decay = tf.minimum(bn_decay_clip, 1 - bn_momentum)
    return bn_decay


def train():
    train_dataset = ModelNetH5Dataset('./data/modelnet40_ply_hdf5_2048/train_files.txt',
                                      batch_size=batch_size, npoints=num_point, shuffle=True)
    test_dataset = ModelNetH5Dataset('data/modelnet40_ply_hdf5_2048/test_files.txt',
                                     batch_size=batch_size, npoints=num_point, shuffle=False)

    point_cloud, labels = placeholder_inputs(batch_size, num_point)
    is_training_pl = tf.placeholder(tf.bool, shape=())

    # Note the global_step=batch parameter to minimize.
    # That tells the optimizer to helpfully increment the 'batch' parameter
    # for you every time it trains.
    batch = tf.get_variable('batch', [],
                            initializer=tf.constant_initializer(0), trainable=False)
    bn_decay = get_bn_decay(batch)
    tf.summary.scalar('bn_decay', bn_decay)

    pred = pointnet2(point_cloud, nb_classes, is_training_pl)

    loss = tf.nn.sparse_softmax_cross_entropy_with_logits(logits=pred, labels=labels)
    classify_loss = tf.reduce_mean(loss)
    tf.summary.scalar('classify loss', classify_loss)
    tf.add_to_collection('losses', classify_loss)
    losses = tf.get_collection('losses')
    total_loss = tf.add_n(losses, name='total_loss')
    tf.summary.scalar('total_loss', total_loss)
    for the_lable in losses + [total_loss]:
        tf.summary.scalar(the_lable.op.name, the_lable)

    correct = tf.equal(tf.argmax(pred, 1), tf.to_int64(labels))
    accuracy = tf.reduce_sum(tf.cast(correct, tf.float32)) / float(batch_size)
    tf.summary.scalar('accuracy', accuracy)

    learning_rate = get_learning_rate(batch)
    tf.summary.scalar('learning_rate', learning_rate)

    train_op = tf.train.AdamOptimizer(learning_rate).minimize(total_loss, global_step=batch)

    saver = tf.train.Saver()

    session = K.get_session()

    merged = tf.summary.merge_all()
    train_writer = tf.summary.FileWriter(os.path.join(summary_dir, 'train'), session.graph)
    test_writer = tf.summary.FileWriter(os.path.join(summary_dir, 'test'), session.graph)

    init_op = tf.global_variables_initializer()
    session.run(init_op)

    with session.as_default():
        with tf.device('/gpu:0'):
            for epoch in range(epochs):
                # TODO: add early stop to prevent overfitting
                print('**** EPOCH %03d ****' % epoch)

                # Make sure batch data is of same size
                cur_batch_data = np.zeros((batch_size, num_point, train_dataset.num_channel()))
                cur_batch_label = np.zeros(batch_size, dtype=np.int32)

                total_correct = 0
                total_seen = 0
                loss_sum = 0
                batch_idx = 0

                while train_dataset.has_next_batch():
                    batch_data, batch_label = train_dataset.next_batch(augment=True)
                    bsize = batch_data.shape[0]
                    cur_batch_data[0:bsize, ...] = batch_data
                    cur_batch_label[0:bsize] = batch_label
                    _, loss_val, pred_val, summary, step = session.run([train_op, total_loss, pred, merged, batch], feed_dict={
                        point_cloud: cur_batch_data,
                        labels: cur_batch_label,
                        is_training_pl: True,
                    })

                    train_writer.add_summary(summary, step)

                    pred_val = np.argmax(pred_val, 1)
                    correct = np.sum(pred_val[0:bsize] == batch_label[0:bsize])
                    total_correct += correct
                    total_seen += bsize
                    loss_sum += loss_val
                    if (batch_idx + 1) % 50 == 0:
                        print(' ---- batch: %03d ----' % (batch_idx + 1))
                        print('mean loss: %f' % (loss_sum / 50))
                        print('accuracy: %f' % (total_correct / float(total_seen)))
                        total_correct = 0
                        total_seen = 0
                        loss_sum = 0
                    batch_idx += 1

                train_dataset.reset()

                # Make sure batch data is of same size
                cur_batch_data = np.zeros((batch_size, num_point, test_dataset.num_channel()))
                cur_batch_label = np.zeros(batch_size, dtype=np.int32)

                total_correct = 0
                total_seen = 0
                loss_sum = 0
                batch_idx = 0
                total_seen_class = [0 for _ in range(nb_classes)]
                total_correct_class = [0 for _ in range(nb_classes)]

                print('---- EPOCH %03d EVALUATION ----' % epoch)

                while test_dataset.has_next_batch():
                    batch_data, batch_label = test_dataset.next_batch(augment=False)
                    bsize = batch_data.shape[0]
                    # for the last batch in the epoch, the bsize:end are from last batch
                    cur_batch_data[0:bsize, ...] = batch_data
                    cur_batch_label[0:bsize] = batch_label

                    _, loss_val, pred_val, summary, step = session.run([train_op, total_loss, pred, merged, batch], feed_dict={
                        point_cloud: cur_batch_data,
                        labels: cur_batch_label,
                        is_training_pl: False,
                    })

                    test_writer.add_summary(summary, step)

                    pred_val = np.argmax(pred_val, 1)
                    correct = np.sum(pred_val[0:bsize] == batch_label[0:bsize])
                    total_correct += correct
                    total_seen += bsize
                    loss_sum += loss_val
                    batch_idx += 1
                    for bindex in range(0, bsize):
                        the_lable = batch_label[bindex]
                        total_seen_class[the_lable] += 1
                        total_correct_class[the_lable] += (pred_val[bindex] == the_lable)

                print('eval mean loss: %f' % (loss_sum / float(batch_idx)))
                print('eval accuracy: %f' % (total_correct / float(total_seen)))
                print('eval avg class acc: %f' % (
                    np.mean(np.array(total_correct_class) / np.array(total_seen_class, dtype=np.float))))

                test_dataset.reset()

                if epoch % 10 == 0:
                    save_path = saver.save(session, os.path.join(summary_dir, "model.ckpt"))
                    print("Model saved in file: {}".format(save_path))


if __name__ == '__main__':
    train()
