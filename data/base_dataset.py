"""This module implements an abstract base class (ABC) 'BaseDataset' for datasets.
It also includes common transformation functions (e.g., get_transform, __scale_width), which can be later used in subclasses.
"""
import random

import torch
import torch.utils.data as data
from abc import ABC, abstractmethod
from math import radians, cos, sin, asin, sqrt
from data.data_util import *

class BaseDataset(data.Dataset, ABC):
    """This class is an abstract base class (ABC) for datasets.
    To create a subclass, you need to implement the following four functions:
    -- <__init__>:                      initialize the class, first call BaseDataset.__init__(self, opt).
    -- <__len__>:                       return the size of dataset.
    -- <__getitem__>:                   get a data point.
    -- <modify_commandline_options>:    (optionally) add dataset-specific options and set default options.
    """

    def __init__(self, opt):
        """Initialize the class; save the options in the class
        Parameters:
            opt (Option class)-- stores all the experiment flags; needs to be a subclass of BaseOptions

        In this function, we instantiate the following variables:
        raw_data -- a dictionary that stores the raw data:
            'pred' -- target variables of shape (num_nodes, num_time_steps, num_features1)
            'feat' (optional) -- covariates of shape (num_nodes, num_time_steps, num_features2)
            'time' -- time stamps of shape (num_time_steps, )
            'missing' -- missing masks of shape (num_nodes, num_time_steps, num_features1)
        A -- adjacency matrix of shape (num_nodes, num_nodes)
        test_node_index -- a numpy array of shape (num_test_nodes, ) that stores the indices of test nodes
        train_node_index -- a numpy array of shape (num_train_nodes, ) that stores the indices of train nodes
        """
        self.opt = opt
        self.time_division = {}
        self.raw_data = {}
        self.A = None
        self.test_node_index = None
        self.train_node_index = None

    @staticmethod
    def modify_commandline_options(parser, is_train):
        """Add new dataset-specific options, and rewrite default values for existing options.
        Parameters:
            parser          -- original option parser
            is_train (bool) -- whether training phase or test phase. You can use this flag to add training-specific or test-specific options.
        Returns:
            the modified parser.
        """
        return parser

    def __len__(self):
        """Return the total number of images in the dataset."""
        # if self.opt.phase == 'train':
        length = self.raw_data['pred'].shape[1] - self.opt.t_len
        # else:
        #     length = int(self.raw_data['pred'].shape[1] / self.opt.t_len)
        return length

    def __getitem__(self, t_index):
        """Return a data point and its metadata information.
        Parameters:
            t_index - - a random integer for data indexing
        Returns:
            a dictionary of data with their names. It ususally contains the data itself and its metadata information.
        """
        # if self.opt.model in ['stdiffusion', 'csdi']:
        node_index = None
        start_index = t_index
        end_index = t_index + self.opt.t_len
        if self.opt.model in ['stdiffusion', 'stdiffusionfore']:
            pred, feat, missing_mask = BaseDataset._fetch_data_item_from_dict(self.raw_data, start_index, end_index, station_index=node_index)
            time = self.raw_data['time'][start_index:end_index]
            A = self.A

            batch_data = {
                'pred': pred.float(),  # [num_n, time, d_x]
                'adj': torch.from_numpy(A).float(),  # [num_n, num_n]
                'missing_mask': missing_mask.float(),  # [num_n, time]
                'time': time,  # [time]
                'context_index': self.train_node_index,
                'target_index': self.test_node_index
            }
            if feat is not None:
                batch_data['feat'] = feat.float()  # [num_n, time, d_s]
        elif self.opt.model in ['hierarchical', 'gsnp', 'adain']:
            """
            In the experiment, I found KL term could always be zero in some situations
            The reason is that the network cannot distinguish the prior and posterior
            One possible solution is to diversify the training data.
            For example, one item samples a different graph / time index
            In the previous experiments, if the graph in a batch is the same, the KL term is always zero
            """
            if self.opt.phase == 'train':
                # random divide nodes into context set and target set
                target_index, context_index = BaseDataset._div_context_target(self.train_node_index, self.opt.num_train_target)
            else:
                target_index, context_index = self.test_node_index, self.train_node_index

            A_1hop = self.A[target_index, :][:, context_index][np.newaxis]  # 1-hop neighbor
            A_2hop = np.dot(self.A, self.A)[target_index, :][:, context_index][np.newaxis]  # 2-hop neighbor
            adj = np.concatenate([A_1hop, A_2hop], axis=0)

            pred_target, feat_target, missing_mask_target = BaseDataset._fetch_data_item_from_dict(self.raw_data, start_index,
                                                                                                   end_index, target_index)
            pred_context, feat_context, missing_mask_context = BaseDataset._fetch_data_item_from_dict(self.raw_data, start_index,
                                                                                                      end_index, context_index)
            time = self.raw_data['time'][start_index:end_index]
            missing_mask_context = missing_mask_context.squeeze(-1)
            missing_mask_target = missing_mask_target.squeeze(-1)
            adj = torch.from_numpy(adj)

            batch_data = {'pred_context': pred_context.float(),  # [num_n, time, d_y]
                          'pred_target': pred_target.float(),  # [num_m, time, d_y]
                          'adj': adj.float(),  # [2, num_m, num_n]
                          'missing_mask_context': missing_mask_context.float(),  # [num_n, time]
                          'missing_mask_target': missing_mask_target.float(),  # [num_m, time]
                          'time': time  # [time]
                          }
            # add features if available
            if feat_context is not None:
                batch_data['feat_context'] = feat_context.float()  # [num_n, time]
                batch_data['feat_target'] = feat_target.float()  # [num_m, time]
            return batch_data
        elif self.opt.model in ['gwavenet', 'wavenet']:
            node_index = np.arange(self.raw_data['pred'].shape[0])
            A = self.A
            if self.opt.phase == 'train':
                node_index = np.random.choice(node_index, int(node_index.size * 0.8), replace=False)
                A = A[node_index][:, node_index]
            pred, feat, missing_mask = BaseDataset._fetch_data_item_from_dict(self.raw_data, start_index, end_index,
                                                                              station_index=node_index)
            batch_data = {
                'pred': pred.float(),  # [num_n, time, d_x]
                'adj': torch.from_numpy(A).float(),  # [num_n, num_n]
                'missing_mask': missing_mask.float(),  # [num_n, time]
            }
            if feat is not None:
                batch_data['feat'] = feat.float()
        else:
            raise NotImplementedError('Data loading for model [{:s}] is not implemented.'.format(self.opt.model))

        return batch_data

    def add_norm_info(self, mean, scale):
        """Add the normalization information of the dataset.
        """
        self.opt.__dict__.update({'mean': mean, 'scale': scale})

    ##################################################
    # utility functions
    ##################################################
    def _data_format_check(self):
        # check raw_data
        if 'pred' not in self.raw_data.keys():
            raise ValueError('raw_data must contain key \'pred\'')
        if 'missing' not in self.raw_data.keys():
            raise ValueError('raw_data must contain key \'missing\'')
        if 'time' not in self.raw_data.keys():
            raise ValueError('raw_data must contain key \'time\'')
        if self.raw_data['pred'].shape != self.raw_data['missing'].shape:
            raise ValueError('pred and missing must have the same shape')
        if 'feat' in self.raw_data.keys():
            if self.raw_data['pred'].shape[:-1] != self.raw_data['feat'].shape[:-1]:
                raise ValueError('pred and feat must have the same shape except the last dimension')
        if not isinstance(self.test_node_index, np.ndarray) or not isinstance(self.train_node_index, np.ndarray):
            raise ValueError('test_node_index and train_node_index must be numpy arrays')
        if len(self.test_node_index.shape) != 1 or len(self.train_node_index.shape) != 1:
            raise ValueError('test_node_index and train_node_index must be 1D arrays')
        # check adjacency matrix
        if not isinstance(self.A, np.ndarray):
            raise ValueError('A must be a numpy array')
        if len(self.A.shape) != 2:
            raise ValueError('A must be a 2D array')
        # norm info check
        if self.opt.mean is None or self.opt.scale is None:
            raise ValueError('mean and scale must be specified')
        print('Data format check passed!!!')

    def haversine(self, lon1, lat1, lon2, lat2):
        """
        Calculate the great circle distance between two points
        on the earth (specified in decimal degrees)
        """
        lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

        # haversine
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        c = 2 * asin(sqrt(a))
        r = 6371
        return c * r  # km

    @staticmethod
    def _div_context_target(train_station_index, num_target):
        """
        For training phase: divide training stations into context and target
        Args:
            train_station_index (ndarray): training node indexes
            num_target: number of target nodes

        Returns:
            target_index (ndarray): target station indexes
            context_index (ndarray): context station indexes
        """
        target_index = np.random.choice(train_station_index, num_target, replace=False)
        context_index = np.setdiff1d(train_station_index, target_index)
        return target_index, context_index

    @staticmethod
    def _get_start_index(index, t_len, phase='train'):
        """
        Get the start index of the time series
        Training phase: current index + t_len
        Test phase: no overlap between time series
        Args:
            index (int): index of the time series
            t_len (int): length of the time series
            phase (str): phase of the model

        Returns:
            start_index (int): start index of the time series
            start_index (int): end index of the time series
        """
        if phase == 'train':
            start_index = index
            end_index = index + t_len
        else:
            start_index = index * t_len
            end_index = start_index + t_len
        return start_index, end_index

    @staticmethod
    def _fetch_data_item_from_dict(data, start_index, end_index, station_index=None):
        """
        Fetch data from the time series
        Args:
            data (dict: {'feat'(optional), 'missing', 'pred'}): time series data dictionary.
            Key feat is optional, depending on the dataset.
            station_index (ndarray): station indexes
            start_index (int): start index of the time series
            end_index (int): end index of the time series

        Returns:
            data_item (tensor): data of the time series
        """
        if station_index is None:
            # return all stations
            station_index = np.arange(data['missing'].shape[0])
        pred = torch.from_numpy(data['pred'][station_index, start_index:end_index])
        feat = torch.from_numpy(data['feat'][station_index, start_index:end_index]) if 'feat' in data.keys() else None
        missing = torch.from_numpy(data['missing'][station_index, start_index:end_index])
        return pred, feat, missing

    # @staticmethod
    # def _fetch_divided_form_data_item(
    #         data,
    #         A,
    #         index,
    #         t_len,
    #         train_node_index,
    #         test_node_index,
    #         num_train_target=None,  # training parameter
    #         phase='train',
    # ):
    #     """
    #     data will be divided into context and target set, following the setting of neural processes
    #     Args:
    #         data: time series data dict (dict{'name': {'feat', 'missing', 'pred'}})
    #         A: adjacency matrix
    #         index: int
    #         t_len: length of time series (int)
    #         num_train_target: number of target nodes for training (int)
    #         train_node_index: training node indexes (ndarray)
    #         test_node_index: testing node indexes (ndarray)
    #         phase: train, val, test (string)
    #
    #     Returns:
    #         data item dictionary (dict {
    #         'x_context', 'y_context', 'x_target',
    #         'y_target', 'adj', 'missing_index_context',
    #         'missing_index_target', 'time'
    #         })
    #     """
    #     if phase == 'train':
    #         # random divide nodes into context set and target set
    #         target_index, context_index = BaseDataset._div_context_target(train_node_index, num_train_target)
    #     else:
    #         target_index, context_index = test_node_index, train_node_index
    #
    #     A_1hop = A[target_index, :][:, context_index][np.newaxis]  # 1-hop neighbor
    #     A_2hop = np.dot(A, A)[target_index, :][:, context_index][np.newaxis]  # 2-hop neighbor
    #     adj = np.concatenate([A_1hop, A_2hop], axis=0)
    #
    #     # start_index, end_index = BaseDataset._get_start_index(index, t_len, phase)
    #     start_index = index
    #     end_index = index + t_len
    #
    #     pred_target, feat_target, missing_mask_target = BaseDataset._fetch_data_item_from_dict(data, start_index, end_index, target_index)
    #     pred_context, feat_context, missing_mask_context = BaseDataset._fetch_data_item_from_dict(data, start_index, end_index, context_index)
    #
    #     time = data['time'][start_index:end_index]
    #
    #     missing_mask_context = missing_mask_context.squeeze(-1)
    #     missing_mask_target = missing_mask_target.squeeze(-1)
    #     adj = torch.from_numpy(adj)
    #
    #     batch_data = { 'pred_context': pred_context.transpose(1, 2).float(),  # [num_n, time, d_y]
    #              'pred_target': pred_target.transpose(1, 2).float(),  # [num_m, time, d_y]
    #              'adj': adj.float(),  # [2, num_m, num_n]
    #              'missing_mask_context': missing_mask_context.transpose(0, 1).float(),  # [num_n, time]
    #              'missing_mask_target': missing_mask_target.transpose(0, 1).float(),  # [num_m, time]
    #              'time': time  # [time]
    #      }
    #     # add features if available
    #     if feat_context is not None:
    #         batch_data['feat_context'] = feat_context.transpose(1, 2).float() # [num_n, time, d_x]
    #         batch_data['feat_target'] = feat_target.transpose(1, 2).float() # [num_m, time, d_x]
    #     return batch_data

    # @staticmethod
    # def _fetch_bunch_data_item(
    #     data,
    #     A,
    #     index,
    #     node_index,
    #     t_len,
    #     pos_enc_dim=64, # for transformer positional embedding
    #     phase='train',
    # ):
    #     """
    #     return a bunch of data items
    #     """
    #     # subgraph sampling
    #     # if phase == 'train':
    #     #     node_index = np.random.choice(node_index, int(node_index.size*0.8), replace=False)
    #     start_index, end_index = BaseDataset._get_start_index(index, t_len, phase)
    #     pred, feat, missing_mask = BaseDataset._fetch_data_item_from_dict(data, start_index, end_index, station_index=node_index) # get all stations
    #     time = data['time'][start_index:end_index]
    #     if node_index is not None:
    #         A = A[node_index, :][:, node_index]
    #
    #     # sparsity is important for transformer
    #     # todo: now only beijing dataset needs this (or not, cannot confirm)
    #     # kmin_A = np.partition(A, int(A.shape[0] * 0.6), axis=1)[:, int(A.shape[0] * 0.6), np.newaxis]
    #     # A = A * (A >= kmin_A)
    #
    #     # Laplacian eigenvectors as Positional Encodings (PE)
    #     # https://arxiv.org/pdf/2003.00982.pdf
    #     flip = True if phase == 'train' else False
    #     spe = torch.from_numpy(laplacian_positional_encoding(A, pos_enc_dim, flip)).float()
    #
    #     # temporal positional encoding
    #     tpe = temporal_positional_embedding(t_len, 128)
    #
    #     # batch data
    #     batch_data = {
    #         'pred': pred.float(),  # [num_n, time, d_x]
    #         'adj': torch.from_numpy(A).float(),  # [num_n, num_n]
    #         'spe': spe,  # [num_n, pos_enc_dim]
    #         'tpe': torch.from_numpy(tpe).float(),  # [time, pos_enc_dim]
    #         'missing_mask': missing_mask.float(),  # [num_n, time]
    #         'time': time  # [time]
    #     }
    #     if feat is not None:
    #         batch_data['feat'] = feat.float()  # [num_n, time, d_s]
    #
    #     return batch_data

    def get_node_division(self, test_nodes_path, num_nodes=None, test_node_ratio=1/3):
        if os.path.isfile(test_nodes_path):
            test_nodes = np.load(test_nodes_path)
        else:
            print('No testing nodes. Randomly divide nodes for testing!')
            rand = np.random.RandomState(4)  # Fixed random output
            test_nodes = np.sort(rand.choice(list(range(0, num_nodes)), int(num_nodes * test_node_ratio + 0.5), replace=False))
            np.save(test_nodes_path, test_nodes)
        return test_nodes
