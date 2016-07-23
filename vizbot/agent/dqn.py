from pprint import pprint
import collections
import numpy as np
import tensorflow as tf
from vizbot.agent import EpsilonGreedy
from vizbot.model import Model, dense, default_network
from vizbot.preprocess import (
    Grayscale, Downsample, FrameSkip, NormalizeReward, NormalizeImage)
from vizbot.utility import Experience, Every, Statistic, Decay, merge_dicts


class DQN(EpsilonGreedy):

    @classmethod
    def defaults(cls):
        # Preprocesses.
        downsample = 2
        frame_skip = 4
        # Exploration.
        epsilon_from = 1.0
        epsilon_to = 0.1
        epsilon_duration = 5e5
        # Learning.
        replay_capacity = int(2e4)
        batch_size = 32
        initial_learning_rate = 1e-4
        optimizer = tf.train.RMSPropOptimizer
        rms_decay= 0.99
        # Logging.
        print_cost = 10000
        start_learning = 100
        save_model = int(1e5)
        return merge_dicts(super().defaults(), locals())

    def __init__(self, trainer, config):
        # Preprocessing.
        trainer.add_preprocess(NormalizeReward)
        trainer.add_preprocess(NormalizeImage)
        trainer.add_preprocess(Grayscale)
        trainer.add_preprocess(Downsample, config.downsample)
        trainer.add_preprocess(FrameSkip, config.frame_skip)
        super().__init__(trainer, config)
        # Network.
        self._actor = Model(self._create_network)
        self._target = Model(self._create_network)
        self._target.weights = self._actor.weights
        print(str(self._actor))
        # Learning.
        self._memory = Experience(config.replay_capacity)
        self._learning_rate = Decay(
            float(config.initial_learning_rate), 0, self._trainer._timesteps)
        self._costs = Statistic('Cost {:8.3f}', self.config.print_cost)

    def __call__(self):
        save_model = Every(self.config.save_model)
        while self._trainer.running:
            if save_model(self._trainer.timestep) and self._trainer.directory:
                self._actor.save(self._trainer.directory, 'model')
            self._trainer.run_episode(self, self._env)

    def _create_network(self, model):
        # Percetion.
        state = model.add_input('state', self.states.shape)
        values = dense(default_network(state), self.actions.shape, tf.identity)
        # Outputs.
        action = model.add_input('action', self.actions.shape)
        target = model.add_input('target')
        model.add_output('value', tf.reduce_max(values, 1))
        model.add_output('choice',
            tf.one_hot(tf.argmax(values, 1), self.actions.shape))
        # Training.
        learning_rate = model.add_option(
            'learning_rate', float(self.config.initial_learning_rate))
        model.set_optimizer(self.config.optimizer(
            learning_rate, self.config.rms_decay))
        model.add_cost('cost',
            (tf.reduce_sum(action * values, 1) - target) ** 2)

    def _step(self, state):
        return self._actor.compute('choice', state=state)

    def _compute_target(self, reward, successor):
        finals = len(list(x for x in successor if x is None))
        if finals:
            print(finals, 'terminal states in current batch')
        future = self._target.compute('value', state=successor)
        final = np.isnan(successor.reshape((len(successor), -1))).any(1)
        future[final] = 0
        target = reward + self.config.discount * future
        return target

    def experience(self, state, action, reward, successor):
        self._memory.append((state, action, reward, successor))
        if len(self._memory) == 1:
            self._log_memory_size()
        if len(self._memory) < self.config.start_learning:
            return
        state, action, reward, successor = \
            self._memory.sample(self.config.batch_size)
        target = self._compute_target(reward, successor)
        self._target.weights = self._actor.weights
        self._actor.set_option('learning_rate',
            self._learning_rate(self.timestep))
        cost = self._actor.train('cost',
            state=state, action=action, target=target)
        self._costs(cost)

    def _log_memory_size(self):
        size = self._memory.nbytes / (1024 ** 3)
        print('Replay memory size', round(size, 2), 'GB')
