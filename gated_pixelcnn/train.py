import os
import argparse
from datetime import datetime
import tensorflow as tf
import tensorflow_datasets as tfds

from model import GatedPixelCNN, bits_per_dim_loss
from utils import PlotSamplesCallback

tfk = tf.keras
tfkl = tf.keras.layers
AUTOTUNE = tf.data.experimental.AUTOTUNE

# Parsing parameters
parser = argparse.ArgumentParser()
parser.add_argument('-e', '--epochs', type=int, default=20, help='Number of training epochs')
parser.add_argument('-b', '--batch', type=int, default=64, help='Training batch size')
parser.add_argument('-bf', '--buffer', type=int, default=1024, help='Buffer size for shiffling')
parser.add_argument('-d', '--dataset', type=str, default='mnist', help='Dataset: cifar10 or mnist')
parser.add_argument('-lr', '--learning_rate', type=float, default=0.001, help='Learning rate')
parser.add_argument('-dc', '--lr_decay', type=float, default=0.999995, help='Learning rate decay')
parser.set_defaults(context=False)
parser.add_argument('-c', '--context', dest='context', action='store_true', help='Enable contextual training')

parser.add_argument('-hd', '--hidden_dim', type=int, default=128, help='Hidden dimension per channel')
parser.add_argument('-n', '--n_res', type=int, default=20, help='Number of res blocks')

args = parser.parse_args()

# Training parameters
EPOCHS = args.epochs
BATCH_SIZE = args.batch
BUFFER_SIZE = args.buffer  # for shuffling

# Load dataset
dataset, info = tfds.load(args.dataset, with_info=True)
train_ds, test_ds = dataset['train'], dataset['test']

def prepare(element):
    image = element['image']
    image = tf.cast(image, tf.float32)
    if args.context is True:
        # OH encode labels
        num_classes = info.features['label'].num_classes
        label = element['label']
        context = tf.one_hot(label, depth=num_classes)
        return (image, context), image
    else:
        # PixelCNN training requires target = input
        return image, image

train_ds = (train_ds.shuffle(BUFFER_SIZE)
                    .batch(BATCH_SIZE)
                    .map(prepare, num_parallel_calls=AUTOTUNE)
                    .prefetch(AUTOTUNE))

test_ds = (test_ds.batch(BATCH_SIZE)
                   .map(prepare, num_parallel_calls=AUTOTUNE)
                   .prefetch(AUTOTUNE))

# Define model
strategy = tf.distribute.MirroredStrategy()
with strategy.scope():
    model = GatedPixelCNN(
        hidden_dim=args.hidden_dim,
        n_res=args.n_res
    )
    model.compile(optimizer='adam', loss=bits_per_dim_loss)

# Learning rate scheduler
steps_per_epochs = info.splits['train'].num_examples // args.batch
decay_per_epoch = args.lr_decay ** steps_per_epochs
schedule = tfk.optimizers.schedules.ExponentialDecay(
    initial_learning_rate=args.learning_rate,
    decay_rate=decay_per_epoch,
    decay_steps=1
)

# Callbacks
time = datetime.now().strftime('%Y%m%d-%H%M%S')
log_dir = os.path.join('.', 'logs', 'gatedpixelcnn', time)
tensorboard_clbk = tfk.callbacks.TensorBoard(log_dir=log_dir)
num_classes = info.features['label'].num_classes if args.context else -1
sample_clbk = PlotSamplesCallback(logdir=log_dir, num_classes=num_classes, nex=8)
scheduler_clbk = tfk.callbacks.LearningRateScheduler(schedule)
callbacks = [tensorboard_clbk, sample_clbk, scheduler_clbk]

# Fit
model.fit(train_ds, validation_data=test_ds, epochs=EPOCHS, callbacks=callbacks)
