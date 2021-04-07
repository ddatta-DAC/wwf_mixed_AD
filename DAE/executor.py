import sys
import os
import pandas as pd
import numpy as np
from sklearn.metrics import auc
sys.path.append('./..')
sys.path.append('./../..')
sys.path.append('./../../..')
try:
    from . import utils
except:
    import utils
import argparse

import torch
from torch import FloatTensor as FT
from torch import LongTensor as LT
from torch import nn
from torch.nn import functional as F
import yaml
import numpy as np
from tqdm import tqdm
import matplotlib.pyplot as plt
import math
import argparse

try:
    from data_fetcher import data_fetcher
except:
    from .data_fetcher import data_fetcher

try:
    from .model import StackedAE
except:
    from model import StackedAE

EPSILON = math.pow(10, -6)
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print('Current device  >> ', DEVICE)
print('=========================== ')

def train_model(
    DATA_SET,
    data_dict,
    config
):
    layer_dims = config['layer_dims']
    train_df = data_dict['train']
    train_X = train_df.values
    data_dim = train_X.shape[1]

    epochs_1 = config['epochs_1']
    epochs_2 = config['epochs_2']

    dae_obj = StackedAE(
        DEVICE,
        data_dim,
        layer_dims,  # Provide the half (encoder only)
        op_activation='sigmoid',
        layer_activation='tanh',
        dropout=0.2,
        LR=0.05,
        num_epochs_1=epochs_1,
        num_epochs_2=epochs_2,
        min_epochs=5,
        batch_size=512,
        stop_threshold=0.05,
        checkpoint_dir=DATA_SET,
    )
    dae_obj.train_model(train_X)

    return dae_obj

def test_eval(
    dae_obj,
    data_dict,
    num_anomaly_sets
):
    test_X = data_dict['test'].values
    test_labels = [0 for _ in range(test_X.shape[0])]
    test_scores = dae_obj.score_samples(test_X)

    auc_result = {}
    for anomaly_key in ['anom_2_', 'anom_3_']:
        auc_list = []
        for idx in range(num_anomaly_sets):
            key = anomaly_key + str(idx+1)
            anom_X = data_dict[key].values
            anom_labels = [1 for _ in range(anom_X.shape[0])]
            anom_scores = dae_obj.score_samples(anom_X)

            combined_scores = np.concatenate([anom_scores, test_scores], axis=0)
            combined_labels = np.concatenate([anom_labels, test_labels], axis=0)

            res_data = []
            for i, j in zip(combined_scores, combined_labels):
                res_data.append((i, j))
            res_df = pd.DataFrame(res_data, columns=['score', 'label'])

            #  Normalize values
            def _normalize_(val, _min, _max):
                return (val - _min) / (_max - _min)

            _max = max(combined_scores)
            _min = min(combined_scores)

            res_df['score'] = res_df['score'].parallel_apply(
                _normalize_,
                args=(_min, _max,)
            )

            res_df = res_df.sort_values(by=['score'], ascending=False)
            _max = max(res_df['score'])
            _min = min(res_df['score'])
            step = (_max - _min) / 100

            # Vary the threshold
            thresh = _max - step
            num_anomalies = anom_X.shape[0]
            P = []
            R = [0]

            while thresh >= _min:
                sel = res_df.loc[res_df['score'] >= thresh]
                if len(sel) == 0:
                    thresh -= step
                    continue
                correct = sel.loc[sel['label'] == 1]
                prec = len(correct) / len(sel)
                rec = len(correct) / num_anomalies
                P.append(prec)
                R.append(rec)
                if rec >= 1.0 :
                    break
                thresh -= step
                thresh = round(thresh,3)

            P = [P[0]] + P
            pr_auc = auc(R, P)
            print("AUC : {:0.4f} ".format(pr_auc))
            auc_list.append(pr_auc)

        mean_auc = np.mean(auc_list)
        print(' (Mean) AUC {:0.4f} '.format(mean_auc))
        auc_result[anomaly_key] = mean_auc

    return auc_result


# ==============================================================

parser = argparse.ArgumentParser(description='Run the model ')
parser.add_argument(
    '--DATA_SET',
    type=str,
    help=' Which data set ?',
    default=None,
    choices=['kddcup', 'kddcup_neptune', 'nsl_kdd', 'nb15', 'gureKDD']
)

parser.add_argument(
    '--num_runs',
    type=int,
    default=10,
    help='Number of runs'
)

args = parser.parse_args()
DATA_SET = args.DATA_SET
num_runs = args.num_runs
LOG_FILE = 'log_results_{}.txt'.format(DATA_SET)
LOGGER = utils.get_logger(LOG_FILE)
utils.log_time(LOGGER)
LOGGER.info(DATA_SET)

config_file = 'config.yaml'
with open(config_file, 'r') as fh:
    config = yaml.safe_load(fh)

num_anomaly_sets = config['num_anomaly_sets']
anomaly_ratio = config['anomaly_ratio']
results = {}

for n in range(1, num_runs + 1):
    data_dict, _ = data_fetcher.get_data(
        DATA_SET,
        one_hot=True,
        num_anom_sets=num_anomaly_sets,
        anomaly_ratio=anomaly_ratio
    )
    dae_obj = train_model(DATA_SET, data_dict, config)
    auc_result = test_eval(dae_obj, data_dict, num_anomaly_sets)

    for key,_aupr in auc_result.items():
        if key not in results.keys():
            results[key] = []
        results[key].append(_aupr)
        LOGGER.info("Run {}:  Anomaly type {} AuPR: {:4f}".format(n, key, _aupr))

#--------------------
for key, _aupr in results.items():
    mean_all_runs = np.mean(_aupr)
    log_op = 'Mean AuPR over {} runs | {} |  {:5f}  Std {:.5f}'.format(num_runs, key, mean_all_runs, np.std(_aupr))
    print(log_op)
    LOGGER.info(log_op)
    LOGGER.info(' Details ' + str(_aupr))

utils.close_logger(LOGGER)