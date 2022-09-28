# -*- coding: utf-8 -*-
"""fb3-deberta-v3-base-layerwise-optuna.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1oCBua3dFNE29sRnyNA5foA2vT3Rz1i4-

# About this notebook

this code was based on FB3 / Deberta-v3-base baseline [train]
# https://www.kaggle.com/code/yasufuminakama/fb3-deberta-v3-base-baseline-train

- Deberta-v3-base starter code
- pip wheels is [here](https://www.kaggle.com/code/yasufuminakama/fb3-pip-wheels)
- Inference notebook is [here](https://www.kaggle.com/yasufuminakama/fb3-deberta-v3-base-baseline-inference)


I thought I would share this notebook with the additions of Layer Wise Learning and Optuna Hyperparameter tuning since the original author of this notebook released it and saved me a great deal of time.  I am new to NLP and Kaggle competitions so I doubt I will be taking any gold medals with my work so might as well share with others that are also new and learning. I have tried to verify the code as accurate please comment or share corrections, bugs are always to be hunted down.

# Imports of Libraries and Modules
"""

# Commented out IPython magic to ensure Python compatibility.
################ NOTES ####################
# this code was based on from FB3 / Deberta-v3-base baseline [train]
# https://www.kaggle.com/code/yasufuminakama/fb3-deberta-v3-base-baseline-train
#
#
#  - see ensemble learning, not yet implemented, see https://www.kaggle.com/code/gilfernandes/commonlit-pytorch-ensemble-large/notebook 
#  - following optuna implementation is based on https://github.com/gilfernandes/commonlit
#  - original optunua notebook at https://github.com/gilfernandes/commonlit/blob/main/72_pytorch_transformers_deberta_optuna.ipynb
#  - see article at https://signal.onepointltd.com/post/102h4el/modern-natural-language-processing-on-kaggle
#  - guide to HF scheduler and differential learning rate https://www.kaggle.com/code/rhtsingh/guide-to-huggingface-schedulers-differential-lrs/notebook
#  - learning rate schedulers https://www.kaggle.com/code/snnclsr/learning-rate-schedulers
#  - optuna toy example https://github.com/optuna/optuna-examples/blob/main/pytorch/pytorch_simple.pyc
#  - K-Folding, https://cran.r-project.org/web/packages/cvms/vignettes/picking_the_number_of_folds_for_cross-validation.html
#  - deberta-v2 documentation: https://huggingface.co/transformers/v4.7.0/model_doc/deberta_v2.html
#  - torch optimization documentation, to adjust activation and learning scheduler https://alband.github.io/doc_view/optim.html
#  - HF, optimization docs, https://huggingface.co/docs/transformers/main_classes/optimizer_schedules
#  - layerwise learning was based on https://towardsdatascience.com/transformers-can-you-rate-the-complexity-of-reading-passages-17c76da3403?sk=0fc1d1199174a065636c186e90342c90
#  - layer wise learning was based on this roberta version, ported to deberta https://github.com/peggy1502/Data-Science-Articles/blob/main/train-roberta-advanced.ipynb
#
###########################################

#TODO https://www.kaggle.com/competitions/feedback-prize-english-language-learning/discussion/350363
'''
this code was based on from FB3 / Deberta-v3-base baseline [train]
https://www.kaggle.com/code/yasufuminakama/fb3-deberta-v3-base-baseline-train


'''


# ====================================================
# Library
# ====================================================
import os
import gc
import re
import ast
import sys
import copy
import json
import time
import math
import string
import pickle
import random
import joblib
import itertools
import warnings
warnings.filterwarnings("ignore")

import scipy as sp
import numpy as np
import pandas as pd
pd.set_option('display.max_rows', 500)
pd.set_option('display.max_columns', 500)
pd.set_option('display.width', 1000)
from tqdm.auto import tqdm
from sklearn.metrics import mean_squared_error
from sklearn.model_selection import StratifiedKFold, GroupKFold, KFold

os.system('pip install iterative-stratification==0.1.7')
from iterstrat.ml_stratifiers import MultilabelStratifiedKFold

import torch
import torch.nn as nn
from torch.nn import Parameter
import torch.nn.functional as F
from torch.optim import Adam, SGD, AdamW
from torch.utils.data import DataLoader, Dataset


os.system('python -m pip install tokenizers')
os.system('python -m pip install transformers')
import tokenizers
import transformers
print(f"tokenizers.__version__: {tokenizers.__version__}")
print(f"transformers.__version__: {transformers.__version__}")
from transformers import AutoTokenizer, AutoModel, AutoConfig
from transformers import get_linear_schedule_with_warmup, get_cosine_schedule_with_warmup, get_cosine_with_hard_restarts_schedule_with_warmup, get_polynomial_decay_schedule_with_warmup
# %env TOKENIZERS_PARALLELISM=true

os.system('python -m pip install optuna')
import optuna
from optuna.trial import TrialState

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
torch.cuda.empty_cache()

# ====================================================
# Directory settings
# ====================================================
import os

OUTPUT_DIR = './'
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

"""# CFG"""

# ====================================================
# CFG
# ====================================================
class CFG:
    wandb=False #set to True or False to use Wandb.ai for metrics
    google_colab=True #set to true if working on google colab
    competition='FB3'
    _wandb_kernel='ell' #wandb.ai setting
    debug=False
    apex=True
    print_freq=20
    num_workers=4
    model="microsoft/deberta-v3-base"
    gradient_checkpointing=True
    scheduler='cosine' # ['linear', 'cosine'] #deprecated for optuna, scheduler set in objective()
    batch_scheduler=True
    num_cycles=0.5
    encoder_lr=1.5e-5 #1.5 @.448 #deprecated for optuna, set in objective()
    decoder_lr=1.5e-5 #deprecated
    min_lr=1e-6 
    eps=1e-6
    betas=(0.9, 0.999) #activaion agruments for optimizer, lookup in docs
    batch_size=2 #originally set to 8
    max_len=512 #originally 512
    weight_decay=0.01
    gradient_accumulation_steps=1
    max_grad_norm=1000
    target_cols=['cohesion', 'syntax', 'vocabulary', 'phraseology', 'grammar', 'conventions']
    seed=1003  #testing 3, 1003
    num_warmup_steps=1 #originally set to 0
    epochs=4 # the internet says this should be 3x num of classes (6) target_cols = 18
    n_fold=4 #originally set to 4 then 5
    #trn_fold = [0,1]
    trn_fold=[0, 1, 2, 3]
    n_trials=5
    train=True
    
    
if CFG.debug:
    CFG.epochs = 1 # 2
    CFG.trn_fold = [0]

# ====================================================
# wandb
# ====================================================
if CFG.wandb:
    os.system('python -m pip install wandb')
    import wandb

    try:
        from kaggle_secrets import UserSecretsClient
        user_secrets = UserSecretsClient()
        secret_value_0 = user_secrets.get_secret("wandb_api")
        wandb.login(key=secret_value_0)
        anony = None
    except:
        anony = "must"
        print('If you want to use your W&B account, go to Add-ons -> Secrets and provide your W&B access token. Use the Label name as wandb_api. \nGet your W&B access token from here: https://wandb.ai/authorize')


    def class2dict(f):
        return dict((name, getattr(f, name)) for name in dir(f) if not name.startswith('__'))

    run = wandb.init(project='ell', 
                     name=CFG.model,
                     config=class2dict(CFG),
                     group=CFG.model,
                     job_type="train",
                     anonymous=anony)

"""# Library"""

# ====================================================
# Directory settings
# ====================================================
import os

if CFG.google_colab:
  # Import from GoogleDrive
  from google.colab import drive
  drive.mount('/content/gdrive')
  os.chdir("//content/gdrive/MyDrive/feedback-prize-english-language-learning")

  save_dir = "/content/gdrive/My Drive/feedback-prize-english-language-learning/submission"
  logs_dir = "/content/gdrive/My Drive/feedback-prize-english-language-learning/logs"
  data_dir = "/content/gdrive/My Drive/feedback-prize-english-language-learning"
  model_dir = "/content/gdrive/My Drive/ml_models/model"
  import os.path
  from os import path

  model_folder = CFG.model.replace('/', '-')
  if path.exists(data_dir + "/" + "trained_" + model_folder) == False:
    os.mkdir(data_dir + "/" + "trained_" + model_folder)
  save_model_dir = data_dir + "/" + "trained_" + model_folder

if(CFG.google_colab):
  CFG.OUTPUT_DIR = save_model_dir
  CFG.DATA_DIR = data_dir
else:
  CFG.OUTPUT_DIR = './' #for running on kaggle
  CFG.DATA_DIR = '../input/feedback-prize-english-language-learning'

OUTPUT_DIR = CFG.OUTPUT_DIR
DATA_DIR = CFG.DATA_DIR

print(OUTPUT_DIR)

"""# Utils"""

# ====================================================
# Utils
# ====================================================
def MCRMSE(y_trues, y_preds):
    scores = []
    idxes = y_trues.shape[1]
    for i in range(idxes):
        y_true = y_trues[:,i]
        y_pred = y_preds[:,i]
        score = mean_squared_error(y_true, y_pred, squared=False) # RMSE
        scores.append(score)
    mcrmse_score = np.mean(scores)
    return mcrmse_score, scores

def SWISH(x):
    s = x* (1/(1+np.exp(-x)))
    return s


def get_score(y_trues, y_preds):
    mcrmse_score, scores = MCRMSE(y_trues, y_preds)
    return mcrmse_score, scores


def get_logger(filename=OUTPUT_DIR+'train'):
    from logging import getLogger, INFO, StreamHandler, FileHandler, Formatter
    logger = getLogger(__name__)
    logger.setLevel(INFO)
    handler1 = StreamHandler()
    handler1.setFormatter(Formatter("%(message)s"))
    handler2 = FileHandler(filename=f"{filename}.log")
    handler2.setFormatter(Formatter("%(message)s"))
    logger.addHandler(handler1)
    logger.addHandler(handler2)
    return logger

LOGGER = get_logger()


def seed_everything(seed=CFG.seed):
    random.seed(seed)
    os.environ['PYTHONHASHSEED'] = str(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.backends.cudnn.deterministic = True
    
seed_everything(seed=CFG.seed)

"""# Data Loading"""

# ====================================================
# Data Loading
# ====================================================


train = pd.read_csv(DATA_DIR + '/train.csv')
test = pd.read_csv(DATA_DIR + '/test.csv')
submission = pd.read_csv(DATA_DIR + '/sample_submission.csv')

train_df = train
val_df = test

print(f"train.shape: {train.shape}")
display(train.head())
print(f"test.shape: {test.shape}")
display(test.head())
print(f"submission.shape: {submission.shape}")
display(submission.head())

"""# CV split"""

# ====================================================
# CV split
# ====================================================
Fold = MultilabelStratifiedKFold(n_splits=CFG.n_fold, shuffle=True, random_state=CFG.seed)
for n, (train_index, val_index) in enumerate(Fold.split(train, train[CFG.target_cols])):
    train.loc[val_index, 'fold'] = int(n)
train['fold'] = train['fold'].astype(int)
display(train.groupby('fold').size())

if CFG.debug:
    display(train.groupby('fold').size())
    train = train.sample(n=1000, random_state=0).reset_index(drop=True)
    display(train.groupby('fold').size())

"""# tokenizer"""

# ====================================================
# tokenizer
# ====================================================
os.system('python -m pip install sentencepiece')
import sentencepiece #deberta specific may not be needed for other models

tokenizer = AutoTokenizer.from_pretrained(CFG.model)
tokenizer.save_pretrained(OUTPUT_DIR+'/tokenizer/')
CFG.tokenizer = tokenizer

"""# Dataset"""

# ====================================================
# Define max_len
# ====================================================
lengths = []
tk0 = tqdm(train['full_text'].fillna("").values, total=len(train))
for text in tk0:
    length = len(tokenizer(text, add_special_tokens=False)['input_ids'])
    lengths.append(length)
CFG.max_len = max(lengths) + 3 # cls & sep & sep
LOGGER.info(f"max_len: {CFG.max_len}")

# ====================================================
# Dataset
# ====================================================
def prepare_input(cfg, text):
    inputs = cfg.tokenizer.encode_plus(
        text, 
        return_tensors=None, 
        add_special_tokens=True, 
        max_length=CFG.max_len,
        pad_to_max_length=True,
        truncation=True
    )
    for k, v in inputs.items():
        inputs[k] = torch.tensor(v, dtype=torch.long)
    return inputs


class TrainDataset(Dataset):
    def __init__(self, cfg, df):
        self.cfg = cfg
        self.texts = df['full_text'].values
        self.labels = df[cfg.target_cols].values

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, item):
        inputs = prepare_input(self.cfg, self.texts[item])
        label = torch.tensor(self.labels[item], dtype=torch.float)
        return inputs, label
    

def collate(inputs):
    mask_len = int(inputs["attention_mask"].sum(axis=1).max())
    for k, v in inputs.items():
        inputs[k] = inputs[k][:,:mask_len]
    return inputs

"""# Model"""

# ====================================================
# Model
# ====================================================
class MeanPooling(nn.Module):
    def __init__(self):
        super(MeanPooling, self).__init__()
        
    def forward(self, last_hidden_state, attention_mask):
        input_mask_expanded = attention_mask.unsqueeze(-1).expand(last_hidden_state.size()).float()
        sum_embeddings = torch.sum(last_hidden_state * input_mask_expanded, 1)
        sum_mask = input_mask_expanded.sum(1)
        sum_mask = torch.clamp(sum_mask, min=1e-9)
        mean_embeddings = sum_embeddings / sum_mask
        return mean_embeddings
    

class CustomModel(nn.Module):
    def __init__(self, cfg, config_path=None, pretrained=False):
        super().__init__()
        self.cfg = cfg
        if config_path is None:
            self.config = AutoConfig.from_pretrained(cfg.model, output_hidden_states=True)
            self.config.hidden_dropout = 0.
            self.config.hidden_dropout_prob = 0.
            self.config.attention_dropout = 0.
            self.config.attention_probs_dropout_prob = 0.
            LOGGER.info(self.config)
        else:
            self.config = torch.load(config_path)
        if pretrained:
            self.model = AutoModel.from_pretrained(cfg.model, config=self.config)
        else:
            self.model = AutoModel(self.config)
        if self.cfg.gradient_checkpointing:
            self.model.gradient_checkpointing_enable()
        self.pool = MeanPooling()
        self.fc = nn.Linear(self.config.hidden_size, 6)
        self._init_weights(self.fc)
        
    def _init_weights(self, module):
        if isinstance(module, nn.Linear):
            module.weight.data.normal_(mean=0.0, std=self.config.initializer_range)
            if module.bias is not None:
                module.bias.data.zero_()
        elif isinstance(module, nn.Embedding):
            module.weight.data.normal_(mean=0.0, std=self.config.initializer_range)
            if module.padding_idx is not None:
                module.weight.data[module.padding_idx].zero_()
        elif isinstance(module, nn.LayerNorm):
            module.bias.data.zero_()
            module.weight.data.fill_(1.0)
        
    def feature(self, inputs):
        outputs = self.model(**inputs)
        last_hidden_states = outputs[0]
        feature = self.pool(last_hidden_states, inputs['attention_mask'])
        return feature

    def forward(self, inputs):
        feature = self.feature(inputs)
        output = self.fc(feature)
        return output

"""# Loss"""

# ====================================================
# Loss
# ====================================================
class RMSELoss(nn.Module):
    def __init__(self, reduction='mean', eps=1e-9):
        super().__init__()
        self.mse = nn.MSELoss(reduction='none')
        self.reduction = reduction
        self.eps = eps

    def forward(self, y_pred, y_true):
        loss = torch.sqrt(self.mse(y_pred, y_true) + self.eps)
        if self.reduction == 'none':
            loss = loss
        elif self.reduction == 'sum':
            loss = loss.sum()
        elif self.reduction == 'mean':
            loss = loss.mean()
        return loss

"""# **Layerwise Learning**

layer wise learning was based on this roberta version, ported to deberta https://github.com/peggy1502/Data-Science-Articles/blob/main/train-roberta-advanced.ipynb

if you use another model you will need to make adjustments based on the model's parameters, model.named_parameters()

There are two layerwise learning functions, one for grouped and one for setting the LR for each individual layer, rather then grouping the layers together. The functions return an optimizer, in this case AdamW which is set called in the objective() function. 
"""

def deberta_base_AdamW_grouped_LLRD(model, lr, debug=False):
        
    opt_parameters = [] # To be passed to the optimizer (only parameters of the layers you want to update).
    debug_param_groups = []
    named_parameters = list(model.named_parameters()) 
    print("model parameters in grouped llrd func")
    print(model.parameters())
    # According to AAAMLP book by A. Thakur, we generally do not use any decay 
    # for bias and LayerNorm.weight layers.
    no_decay = ["bias", "LayerNorm.bias", "LayerNorm.weight"]
    set_2 = ["layer.4", "layer.5", "layer.6", "layer.7"]
    set_3 = ["layer.8", "layer.9", "layer.10", "layer.11"]
    #init_lr = 1e-6
    init_lr = lr
    
    for i, (name, params) in enumerate(named_parameters):  
        
        weight_decay = 0.0 if any(p in name for p in no_decay) else 0.01
        
        if name.startswith("transformer_model.deberta.embeddings") or name.startswith("transformer_model.deberta.encoder"):            
            # For first set, set lr to 1e-6 (i.e. 0.000001)
            lr = init_lr       
            
            # For set_2, increase lr to 0.00000175
            lr = init_lr * 1.75 if any(p in name for p in set_2) else lr
            
            # For set_3, increase lr to 0.0000035 
            lr = init_lr * 3.5 if any(p in name for p in set_3) else lr
            
            opt_parameters.append({"params": params,
                                   "weight_decay": weight_decay,
                                   "lr": lr})  
            
        # For regressor and pooler, set lr to 0.0000036 (slightly higher than the top layer). 
        #transformer_model.pooler.dense.weight               
        elif name.startswith("regressor") or name.startswith("transformer_model.pooler"):               
            lr = init_lr * 3.6 
            
            opt_parameters.append({"params": params,
                                   "weight_decay": weight_decay,
                                   "lr": lr}) 
        else:
            lr = init_lr 
            
            opt_parameters.append({"params": params,
                                   "weight_decay": weight_decay,
                                   "lr": lr})
            
         

        debug_param_groups.append(f"{i} {name}")
    
    if debug: 
        for g in range(len(debug_param_groups)): print(debug_param_groups[g]) 

    return transformers.AdamW(opt_parameters, lr=init_lr), debug_param_groups  #returns a list opt_parameters

def deberta_base_AdamW_LLRD(model, lr, debug=False):
    #optimal learning rates, https://www.jeremyjordan.me/nn-learning-rate/

    opt_parameters = [] # To be passed to the optimizer (only parameters of the layers you want to update).
    named_parameters = list(model.named_parameters()) 
    debug_param_groups = []
    
    #print(named_parameters)
    # According to AAAMLP book by A. Thakur, we generally do not use any decay 
    # for bias and LayerNorm.weight layers.
    no_decay = ["bias", "LayerNorm.bias", "LayerNorm.weight"]
    init_lr = lr 
    head_lr = lr #needs to be a bit higher then init_lr, i.e. init_lr 3.5e5 at head 3.6e5
    
    
    # === Pooler and regressor ======================================================  
    
    params_0 = [p for n,p in named_parameters if ("pooler" in n or "regressor" in n) 
                and any(nd in n for nd in no_decay)]
    params_1 = [p for n,p in named_parameters if ("pooler" in n or "regressor" in n)
                and not any(nd in n for nd in no_decay)]
    
    head_params = {"params": params_0, "lr": head_lr, "weight_decay": 0.0}    
    opt_parameters.append(head_params)
    debug_param_groups.append(f"head_params")
    
    head_params = {"params": params_1, "lr": head_lr, "weight_decay": 0.01}    
    opt_parameters.append(head_params)
    debug_param_groups.append(f"head_params")
            
    
    # === 12 Hidden layers ==========================================================
    
    for layer in range(11,-1,-1):
        
        params_0 = [p for n,p in named_parameters if f"encoder.layer.{layer}." in n 
                    and any(nd in n for nd in no_decay)]
        params_1 = [p for n,p in named_parameters if f"encoder.layer.{layer}." in n 
                    and not any(nd in n for nd in no_decay)]
        
        layer_params = {"params": params_0, "lr": lr, "weight_decay": 0.0}
        opt_parameters.append(layer_params)   
        debug_param_groups.append(f"layer.{layer}")
                    
        layer_params = {"params": params_1, "lr": lr, "weight_decay": 0.01}
        opt_parameters.append(layer_params)
        debug_param_groups.append(f"layer.{layer}")       
        
        lr *= 0.9 
    
    # === Embeddings layer ==========================================================
    
    params_0 = [p for n,p in named_parameters if "embeddings" in n 
                and any(nd in n for nd in no_decay)]
    params_1 = [p for n,p in named_parameters if "embeddings" in n
                and not any(nd in n for nd in no_decay)]
    
    embed_params = {"params": params_0, "lr": lr, "weight_decay": 0.0} 
    opt_parameters.append(embed_params)
    debug_param_groups.append(f"embed_params")
    
    embed_params = {"params": params_1, "lr": lr, "weight_decay": 0.01} 
    opt_parameters.append(embed_params)
    debug_param_groups.append(f"embed_params")
    
    if debug: 
        for g in range(len(debug_param_groups)): print(g, debug_param_groups[g]) 

    return transformers.AdamW(opt_parameters, lr=init_lr), debug_param_groups

def collect_lr_by_layers(optimizer, grouped_LLRD=True):    
    lr = []
    if grouped_LLRD:
        lr.append(optimizer.param_groups[0]["lr"])   # embeddings
        lr.append(optimizer.param_groups[3]["lr"])   # layer0
        lr.append(optimizer.param_groups[19]["lr"])  # layer1
        lr.append(optimizer.param_groups[35]["lr"])  # layer2
        lr.append(optimizer.param_groups[51]["lr"])  # layer3
        lr.append(optimizer.param_groups[67]["lr"])  # layer4
        lr.append(optimizer.param_groups[83]["lr"])  # layer5
        lr.append(optimizer.param_groups[99]["lr"]) # layer6
        lr.append(optimizer.param_groups[115]["lr"]) # layer7
        lr.append(optimizer.param_groups[131]["lr"]) # layer8
        lr.append(optimizer.param_groups[147]["lr"]) # layer9
        lr.append(optimizer.param_groups[163]["lr"]) # layer10
        lr.append(optimizer.param_groups[179]["lr"]) # layer11
        lr.append(optimizer.param_groups[198]["lr"]) # pooler
        lr.append(optimizer.param_groups[206]["lr"]) # regressor 
    
    else:

        lr.append(optimizer.param_groups[26]["lr"]) # embeddings
        lr.append(optimizer.param_groups[24]["lr"]) # layer0
        lr.append(optimizer.param_groups[22]["lr"]) # layer1
        lr.append(optimizer.param_groups[20]["lr"]) # layer2
        lr.append(optimizer.param_groups[18]["lr"]) # layer3
        lr.append(optimizer.param_groups[16]["lr"]) # layer4
        lr.append(optimizer.param_groups[14]["lr"]) # layer5
        lr.append(optimizer.param_groups[12]["lr"]) # layer6
        lr.append(optimizer.param_groups[10]["lr"]) # layer7
        lr.append(optimizer.param_groups[8]["lr"])  # layer8
        lr.append(optimizer.param_groups[6]["lr"])  # layer9
        lr.append(optimizer.param_groups[4]["lr"])  # layer10
        lr.append(optimizer.param_groups[2]["lr"])  # layer11
        lr.append(optimizer.param_groups[0]["lr"])  # pooler
        lr.append(optimizer.param_groups[0]["lr"])  # regressor 
    return lr 
'''
this function was used to write ouput of paremeters to a csv file, not implemented in this notebook


lr_list = []
lr_list2 = []
#lr_list.append(optimizer.param_groups[0]["lr"])
optimizer, debug = deberta_base_AdamW_LLRD(sample_model) # for per layer lr
optimizer2, debug2 = deberta_base_AdamW_grouped_LLRD(sample_model) # for grouped lr
print("optimizer type")
print(type(optimizer))
#print("debug")
#print(debug)
#collect_lr_by_layers(optimizer, grouped_LLRD=True)


#lr_list2.append(collect_lr_by_layers(optimizer, grouped_LLRD=True))
lr_list2 = collect_lr_by_layers(optimizer, grouped_LLRD=False)
print(len(lr_list))
print(len(lr_list2))
print(lr_list2)

lr_list2 = collect_lr_by_layers(optimizer2, grouped_LLRD=True)
print(len(lr_list))
print(len(lr_list2))
print(lr_list2)
'''

"""# Helpler functions"""

# ====================================================
# Helper functions
# ====================================================
class AverageMeter(object):
    """Computes and stores the average and current value"""
    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


def asMinutes(s):
    m = math.floor(s / 60)
    s -= m * 60
    return '%dm %ds' % (m, s)


def timeSince(since, percent):
    now = time.time()
    s = now - since
    es = s / (percent)
    rs = es - s
    return '%s (remain %s)' % (asMinutes(s), asMinutes(rs))


def train_fn(fold, train_loader, model, criterion, optimizer, epoch, scheduler, device):
    print("TRAIN_FN()")
    model.train()
    scaler = torch.cuda.amp.GradScaler(enabled=CFG.apex)
    losses = AverageMeter()
    start = end = time.time()
    global_step = 0
    for step, (inputs, labels) in enumerate(train_loader):
        inputs = collate(inputs)
        for k, v in inputs.items():
            inputs[k] = v.to(device)
        labels = labels.to(device)
        batch_size = labels.size(0)
        with torch.cuda.amp.autocast(enabled=CFG.apex):
            y_preds = model(inputs)
            loss = criterion(y_preds, labels)
        if CFG.gradient_accumulation_steps > 1:
            loss = loss / CFG.gradient_accumulation_steps
        losses.update(loss.item(), batch_size)
        scaler.scale(loss).backward()
        grad_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), CFG.max_grad_norm)
        if (step + 1) % CFG.gradient_accumulation_steps == 0:
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad()
            global_step += 1
            if CFG.batch_scheduler:
                scheduler.step()
        end = time.time()
        if step % CFG.print_freq == 0 or step == (len(train_loader)-1):
            print('Epoch: [{0}][{1}/{2}] '
                  'Elapsed {remain:s} '
                  'Loss: {loss.val:.4f}({loss.avg:.4f}) '
                  'Grad: {grad_norm:.4f}  '
                  'LR: {lr:.8f}  '
                  .format(epoch+1, step, len(train_loader), 
                          remain=timeSince(start, float(step+1)/len(train_loader)),
                          loss=losses,
                          grad_norm=grad_norm,
                          lr=scheduler.get_lr()[0]))
        if CFG.wandb:
            wandb.log({f"[fold{fold}] loss": losses.val,
                       f"[fold{fold}] lr": scheduler.get_lr()[0]})
    return losses.avg


def valid_fn(valid_loader, model, criterion, device):
    print("valid_FN()")
    losses = AverageMeter()
    model.eval()
    preds = []
    start = end = time.time()
    for step, (inputs, labels) in enumerate(valid_loader):
        inputs = collate(inputs)
        for k, v in inputs.items():
            inputs[k] = v.to(device)
        labels = labels.to(device)
        batch_size = labels.size(0)
        with torch.no_grad():
            y_preds = model(inputs)
            loss = criterion(y_preds, labels)
        if CFG.gradient_accumulation_steps > 1:
            loss = loss / CFG.gradient_accumulation_steps
        losses.update(loss.item(), batch_size)
        preds.append(y_preds.to('cpu').numpy())
        end = time.time()
        if step % CFG.print_freq == 0 or step == (len(valid_loader)-1):
            print('EVAL: [{0}/{1}] '
                  'Elapsed {remain:s} '
                  'Loss: {loss.val:.4f}({loss.avg:.4f}) '
                  .format(step, len(valid_loader),
                          loss=losses,
                          remain=timeSince(start, float(step+1)/len(valid_loader))))
    predictions = np.concatenate(preds)
    return losses.avg, predictions

"""# train loop"""

# ====================================================
# train loop
# ====================================================
def train_loop(folds, fold, model, optimizer, scheduler, scaler, batch_size):
    
    LOGGER.info(f"========== fold: {fold} training ==========")

    # ====================================================
    # loader
    # ====================================================
    
    train_folds = folds[folds['fold'] != fold].reset_index(drop=True)
    valid_folds = folds[folds['fold'] == fold].reset_index(drop=True)
    valid_labels = valid_folds[CFG.target_cols].values
    
    train_dataset = TrainDataset(CFG, train_folds)
    valid_dataset = TrainDataset(CFG, valid_folds)

    train_loader = DataLoader(train_dataset,
                              batch_size=batch_size,
                              shuffle=True,
                              num_workers=CFG.num_workers, pin_memory=True, drop_last=True)
    valid_loader = DataLoader(valid_dataset,
                              batch_size=batch_size * 2,
                              shuffle=False,
                              num_workers=CFG.num_workers, pin_memory=True, drop_last=False)


    # ====================================================
    # model & optimizer
    # ====================================================
    #model = CustomModel(CFG, config_path=None, pretrained=True)
    torch.save(model.config, OUTPUT_DIR+'/config.pt')
    model.to(device)


    # ====================================================
    # loop
    # ====================================================
    criterion = nn.SmoothL1Loss(reduction='mean', beta = 100) # beta = 0.025 .450 RMSELoss(reduction="mean") #added beta argument not in original notebook
    
    best_score = np.inf

    for epoch in range(CFG.epochs):

        start_time = time.time()

        # train
        avg_loss = train_fn(fold, train_loader, model, criterion, optimizer, epoch, scheduler, device)

        # eval
        avg_val_loss, predictions = valid_fn(valid_loader, model, criterion, device)
        
        # scoring
        score, scores = get_score(valid_labels, predictions)
        print("Score")
        print(score)

        elapsed = time.time() - start_time

        LOGGER.info(f'Epoch {epoch+1} - avg_train_loss: {avg_loss:.4f}  avg_val_loss: {avg_val_loss:.4f}  time: {elapsed:.0f}s')
        LOGGER.info(f'Epoch {epoch+1} - Score: {score:.4f}  Scores: {scores}')
        if CFG.wandb:
            wandb.log({f"[fold{fold}] epoch": epoch+1, 
                       f"[fold{fold}] avg_train_loss": avg_loss, 
                       f"[fold{fold}] avg_val_loss": avg_val_loss,
                       f"[fold{fold}] score": score})
        
        if best_score > score:
            best_score = score
            LOGGER.info(f'Epoch {epoch+1} - Save Best Score: {best_score:.4f} Model')
            torch.save({'model': model.state_dict(),
                        'predictions': predictions},
                        OUTPUT_DIR+f"/{CFG.model.replace('/', '-')}_fold{fold}_best.pt")
                        

    predictions = torch.load(OUTPUT_DIR+f"/{CFG.model.replace('/', '-')}_fold{fold}_best.pt", 
                             map_location=torch.device('cpu'))['predictions']
    valid_folds[[f"pred_{c}" for c in CFG.target_cols]] = predictions

    torch.cuda.empty_cache()
    gc.collect()
    
    return valid_folds, best_score

"""# Optuna: Tuning Hyperparameters Section

original optunua notebook at https://github.com/gilfernandes/commonlit/blob/main/72_pytorch_transformers_deberta_optuna.ipynb

Optuna documentation: https://optuna.readthedocs.io/en/stable/
"""

def set_random_seed(random_seed):
    random.seed(random_seed)
    np.random.seed(random_seed)
    os.environ["PYTHONHASHSEED"] = str(random_seed)

    torch.manual_seed(random_seed)
    torch.cuda.manual_seed(random_seed)
    torch.cuda.manual_seed_all(random_seed)

    torch.backends.cudnn.deterministic = True

kfold = KFold(n_splits=CFG.n_fold, random_state=CFG.seed, shuffle=True)
splits = list(kfold.split(train_df))

#fold = 0

def objective(trial):
    epochs = CFG.epochs  #4

    #tuning the learning rate
    lr = trial.suggest_uniform("lr", 1.25e-5, 2.5e-5)

    #tuning lr scheduler hyperparameter
    schedule_func = trial.suggest_categorical('schedule_func', [get_cosine_with_hard_restarts_schedule_with_warmup, get_cosine_schedule_with_warmup, get_polynomial_decay_schedule_with_warmup])

    #tuning batch size hyperparameter
    batch_size = trial.suggest_categorical("batch_size", [2, 4, 8])


    print(f'##### Using fold {fold}')
    print(f'##### Using base_lr {lr}  epochs {epochs}')
    print(f'##### Using {schedule_func}')
    

    model_path = CFG.model

    set_random_seed(CFG.seed + fold)
    
    tokenizer = AutoTokenizer.from_pretrained(CFG.model)


    print("TRAIN var")
    print(train)

    
    model = CustomModel(CFG, config_path=None, pretrained=True)
  
    optimizer, debug = deberta_base_AdamW_LLRD(model, lr) # for per layer lr
    #optimizer, debug = deberta_base_AdamW_grouped_LLRD(model, lr) # for grouped lr
    
    scheduler = schedule_func(optimizer, num_training_steps=CFG.epochs * len(train), num_warmup_steps=CFG.num_warmup_steps) #num_warmup_steps=50
    scaler = torch.cuda.amp.GradScaler() # fp16

    trainer, score = train_loop(train, fold, model, optimizer, scheduler, scaler, batch_size) 
    # Handle pruning based on the intermediate value.
    if trial.should_prune():
      raise optuna.exceptions.TrialPruned()
    
    
    del model
    del tokenizer
    del optimizer

    torch.cuda.empty_cache()
    gc.collect()

    return score #return learning rate also

if __name__ == '__main__':


  for i in CFG.trn_fold:
    fold = i
    study = optuna.create_study(direction="minimize")
    study.optimize(objective, n_trials=CFG.n_trials) #n_trials=20

    pruned_trials = study.get_trials(deepcopy=False, states=[TrialState.PRUNED])
    complete_trials = study.get_trials(deepcopy=False, states=[TrialState.COMPLETE])

    print("Study statistics: ")
    print("  Number of finished trials: ", len(study.trials))
    print("  Number of pruned trials: ", len(pruned_trials))
    print("  Number of complete trials: ", len(complete_trials))

    print("Best trial:")
    trial = study.best_trial

    print("  Value: ", trial.value)

    print("  Params: ")
    for key, value in trial.params.items():
        print("    {}: {}".format(key, value))


    print(" Best value: ", study.best_trial.value)
    print(" Best  params: ")
    for key, value in study.best_trial.params.items():
        print(f"    {key}: {value}")



    if CFG.wandb:
        wandb.finish()

'''
output upon completion:

[I 2022-09-27 23:26:23,723] Trial 1 finished with value: 0.4850880549344474 and parameters: {'lr': 1.6550251604962863e-05, 'schedule_func': <function get_cosine_with_hard_restarts_schedule_with_warmup at 0x7fb83ad19a70>, 'batch_size': 8}. Best is trial 0 with value: 0.4750748158409344.
Study statistics: 
  Number of finished trials:  2
  Number of pruned trials:  0
  Number of complete trials:  2
Best trial:
  Value:  0.4750748158409344
  Params: 
    lr: 1.321538910821276e-05
    schedule_func: <function get_polynomial_decay_schedule_with_warmup at 0x7fb83ad19b00>
    batch_size: 2
 Best value:  0.4750748158409344
 Best  params: 
    lr: 1.321538910821276e-05
    schedule_func: <function get_polynomial_decay_schedule_with_warmup at 0x7fb83ad19b00>
    batch_size: 2

'''

#./microsoft-deberta-v3-base_fold2_best.pth