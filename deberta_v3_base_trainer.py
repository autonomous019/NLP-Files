# -*- coding: utf-8 -*-
"""deberta-v3-base-trainer.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1Cz8IE-JLLb0Xp1gAxcki7pGEuiA_pBTs
"""

!pip install optuna

################ NOTES #####################
#  - based on https://github.com/gilfernandes/commonlit/blob/main/53_pytorch_transformers_deberta_large.ipynb
#
#
#
#
#
#
############################################


import numpy as np # linear algebra
import pandas as pd # data processing, CSV file I/O (e.g. pd.read_csv)

import gc, warnings, random, time, os

from pathlib import Path

from tqdm.notebook import tqdm

warnings.filterwarnings('ignore')

import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from torch.optim import Adam, lr_scheduler
from torch.utils.data import Dataset, DataLoader
from transformers import AdamW
from transformers import AutoModel, AutoTokenizer, AutoConfig
from transformers import get_cosine_schedule_with_warmup

from sklearn.metrics import mean_squared_error
from sklearn.model_selection import KFold

import seaborn as sns

import gc
gc.enable()

import optuna

import os

OUTPUT_DIR = './'
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR
     
TOKENIZERS_PARALLELISM = False
                
import os
os.environ["TOKENIZERS_PARALLELISM"] = "false"
                
'''
huggingface/tokenizers: The current process just got forked, after parallelism has already been used. Disabling parallelism to avoid deadlocks...
To disable this warning, you can either:
	- Avoid using `tokenizers` before the fork if possible
	- Explicitly set the environment variable TOKENIZERS_PARALLELISM=(true | false)



'''

"""### Folders and Dataframes"""

DATA_PATH = "../input/feedback-prize-english-language-learning"
#assert DATA_PATH.exists()
MODELS_PATH = "../input/debertav3base"
#if not MODELS_PATH.exists():
#    os.mkdir(MODELS_PATH)
#assert MODELS_PATH.exists()

train_df = pd.read_csv(DATA_PATH + '/train.csv')
test_df = pd.read_csv(DATA_PATH + '/test.csv')
sample_df = pd.read_csv(DATA_PATH + '/sample_submission.csv')

def remove_unnecessary(df):
    df.drop(df[df['syntax'] == 0].index, inplace=True)
    df.reset_index(drop=True, inplace=True)
    
remove_unnecessary(train_df)

train_df

"""### Config and Seeding"""

class Config(): 
    NUM_FOLDS = 6
    NUM_EPOCHS = 3
    BATCH_SIZE = 2
    MAX_LEN = 248  #experiment with 512
    EVAL_SCHEDULE = [(0.50, 16), (0.49, 8), (0.48, 4), (0.47, 2), (-1., 1)]
    ROBERTA_PATH = 'microsoft/deberta-v3-base'
    TOKENIZER_PATH = 'microsoft/deberta-v3-base'
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    SEED = 3
    NUM_WORKERS = 2
    MODEL_FOLDER = MODELS_PATH #MODELS_PATH = "../input/debertav3base"
    model_name = 'microsoft/deberta-v3-base'
    svm_kernels = ['rbf']
    svm_c = 5
    target_cols=['cohesion', 'syntax', 'vocabulary', 'phraseology', 'grammar', 'conventions']

cfg = Config()

#if not cfg.MODEL_FOLDER.exists():
#    os.mkdir(cfg.MODEL_FOLDER)

def set_random_seed(random_seed):
    random.seed(random_seed)
    np.random.seed(random_seed)
    os.environ["PYTHONHASHSEED"] = str(random_seed)

    torch.manual_seed(random_seed)
    torch.cuda.manual_seed(random_seed)
    torch.cuda.manual_seed_all(random_seed)

    torch.backends.cudnn.deterministic = True

"""### Dataset"""

def add_bins(train_df, num_bins):
    train_df.loc[:, 'bins'] = pd.cut(train_df['syntax'], bins=num_bins, labels=False)
    return num_bins

add_bins(train_df, cfg.NUM_FOLDS)

train_df.groupby(['bins'])[cfg.target_cols].agg(['count', 'mean'])

tokenizer = AutoTokenizer.from_pretrained(cfg.TOKENIZER_PATH)

#get target_cols
target_cols=['cohesion', 'syntax', 'vocabulary', 'phraseology', 'grammar', 'conventions']
class CommonLitDataset(Dataset):
    def __init__(self, df, tokenizer, inference_only=False):
        super().__init__()
        self.df, self.inference_only = df, inference_only
        self.text = df['full_text'].tolist()
        self.bins = df['bins']
        if not inference_only:
            self.target = torch.tensor(df['syntax'].to_numpy(), dtype = torch.float32)
        
        self.encoded = tokenizer.batch_encode_plus(
            self.text,
            padding = 'max_length',
            max_length = cfg.MAX_LEN,
            truncation = True,
            return_attention_mask=True
        )
        
    def __getitem__(self, index):        
        input_ids = torch.tensor(self.encoded['input_ids'][index])
        attention_mask = torch.tensor(self.encoded['attention_mask'][index])
        
        if self.inference_only:
            return {'input_ids': input_ids, 'attention_mask': attention_mask}
        else:
            target = self.target[index]
            return {'input_ids': input_ids, 'attention_mask': attention_mask, 'target': target}
    
    def __len__(self):
        return len(self.df)

sample_ds = CommonLitDataset(train_df, tokenizer)

"""### Model"""

class AttentionHead(nn.Module):
    
    def __init__(self, in_features, hidden_dim, num_targets):
        super().__init__()
        self.in_features = in_features
        
        self.hidden_layer = nn.Linear(in_features, hidden_dim)
        self.final_layer = nn.Linear(hidden_dim, num_targets)
        self.out_features = hidden_dim
        
    def forward(self, features):
        att = torch.tanh(self.hidden_layer(features))
        score = self.final_layer(att)
        attention_weights = torch.softmax(score, dim=1)
        return attention_weights

class CommonLitModel(nn.Module):
    def __init__(self):
        super(CommonLitModel, self).__init__()
        config = AutoConfig.from_pretrained(cfg.ROBERTA_PATH)
        config.update({
            "output_hidden_states": True,
            "hidden_dropout_prob": 0.0,
            "layer_norm_eps": 1e-7
        })
        self.transformer_model = AutoModel.from_pretrained(cfg.ROBERTA_PATH, config=config)
        self.attention = AttentionHead(config.hidden_size, 512, 1)
        self.regressor = nn.Linear(config.hidden_size, 1)
    
    def forward(self, input_ids, attention_mask):
        last_layer_hidden_states = self.transformer_model(input_ids=input_ids, attention_mask=attention_mask)['last_hidden_state']
        weights = self.attention(last_layer_hidden_states)
        context_vector = torch.sum(weights * last_layer_hidden_states, dim=1) 
        return self.regressor(context_vector), context_vector

sample_model = CommonLitModel()

import re

for i, (name, param) in enumerate(sample_model.named_parameters()):
    if(name.find('layer') > -1):
        layer_name = re.sub(r'.+(layer\.\d+).+', r'\1', name)

for i, (name, param) in enumerate(sample_model.named_parameters()):
    print(i, name, param.size())

#experiment and change this to 6,248 when adding target_cols to commonlitdataset()

sample_input_ids = torch.randint(0, 1000, [8, 248])
sample_attention_mask = torch.randint(0, 1000, [8, 248])

sample_model(sample_input_ids, sample_attention_mask)[1].shape

torch.sum(torch.randn([8, 496, 768]), axis=1)

"""### Evaluation and Prediction"""

def eval_mse(model, data_loader):
    model.eval()
    mse_sum = 0
    mse_loss = nn.MSELoss(reduction='sum')
    
    with torch.no_grad():
        for batch_num, record in enumerate(data_loader):
            input_ids, attention_mask, target = record['input_ids'].to(cfg.DEVICE), record['attention_mask'].to(cfg.DEVICE), record['target'].to(cfg.DEVICE)
            pred, _ = model(input_ids, attention_mask)
            mse_sum += mse_loss(pred.flatten().cpu(), target.cpu())
            
    return mse_sum / len(data_loader.dataset)

def predict(model, data_loader):
    model.eval()
    result = []
    
    with torch.no_grad():
        for batch_num, record in tqdm(enumerate(data_loader), total=len(data_loader)):
            input_ids, attention_mask = record['input_ids'].to(cfg.DEVICE), record['attention_mask'].to(cfg.DEVICE)
            pred, _ = model(input_ids, attention_mask)
            result.extend(pred.flatten().to("cpu").tolist())
            
    return np.array(result)

sample_dl = DataLoader(sample_ds, shuffle=False, batch_size=16, num_workers=1)

"""### Optimizer and Sampler"""

5e-5 / 2.5, 5e-5 / 0.5, 5e-5

def create_optimizer(model, base_lr=5e-5, last_lr=None):
    
    #layer wise learning. numerical arguments are from model.named_parameters, see above for list
    named_parameters = list(model.named_parameters())
    
    attention_param_start = 194 #end of last layer, 0-11 layers, layer 11 end
    regressor_param_start = 206 #named parameter regressor.weight
    roberta_parameters = named_parameters[:198] #transformer_model.pooler.dense.weight
    attention_parameters = named_parameters[202:regressor_param_start] #attention.hidden_layer_weight
    regressor_parameters = named_parameters[regressor_param_start:]
    
    attention_group = [params for (name, params) in attention_parameters]
    regressor_group = [params for (name, params) in regressor_parameters]
    
    parameters = []
    if last_lr is not None:
        parameters.append({"params": attention_group, "lr": last_lr})
        parameters.append({"params": regressor_group, "lr": last_lr})
    else:
        parameters.append({"params": attention_group})
        parameters.append({"params": regressor_group})
        
    # Change on different models
    layer_low_threshold = 99
    layer_middle_threshold = 130
    
    for layer_num, (name, params) in enumerate(roberta_parameters):
        weight_decay = 0.0 if 'bias' in name else 0.01
        
        lr = base_lr / 2.5 # 2e-05
        if layer_num >= layer_middle_threshold:
            lr = base_lr / 0.5 # 1e-4
        elif layer_num >= layer_low_threshold:        
            lr = base_lr    
            
        parameters.append({"params": params,
                           "weight_decay": weight_decay,
                           "lr": lr})
        
    return AdamW(parameters)

sample_optimizer = create_optimizer(sample_model)

from torch.utils.data import Sampler,SequentialSampler,RandomSampler,SubsetRandomSampler
from collections import Counter

class WeightedSampler(Sampler):
    
    def __init__(self, dataset):
        
        self.indices = list(range(len(dataset)))
        self.num_samples = len(dataset)
        self.label_to_count = dict(Counter(dataset.bins))
        weights = [1/self.label_to_count[i] for i in dataset.bins]
        
        self.weights = torch.tensor(weights,dtype=torch.double)
        
    def __iter__(self):
        count = 0
        index = [self.indices[i] for i in torch.multinomial(self.weights, self.num_samples, replacement=True)]
        while count < self.num_samples:
            yield index[count]
            count += 1
    
    def __len__(self):
        return self.num_samples

"""### Training"""

def choose_eval_period(val_rmse):
    for rmse, period in cfg.EVAL_SCHEDULE:
        if val_rmse >= rmse:
            return period

def serialize_best(best_val_rmse, best_epoch, val_rmse, epoch, model, model_path):
    if not best_val_rmse or val_rmse < best_val_rmse:
        best_val_rmse = val_rmse
        best_epoch = epoch
        #if not model_path.parent.exists():
        #    os.makedirs(model_path.parent)
        
        #torch.save(model.state_dict(), model_path)
        torch.save(model.state_dict(), OUTPUT_DIR + "/" + f"{cfg.model_name.replace('/', '-')}_fold{fold}_best.pt")

        print(f"New best_val_rmse: {best_val_rmse:0.4}")
    else:       
        print(f"Still best_val_rmse: {best_val_rmse:0.4}",
              f"(from epoch {best_epoch})")
    return best_epoch, best_val_rmse

class Trainer():
    def __init__(self, scaler, model, model_path, train_loader, val_loader, optimizer, scheduler=None, num_epochs=cfg.NUM_EPOCHS):
        self.scaler, self.model, self.model_path, self.train_loader, self.val_loader, self.optimizer, self.scheduler, self.num_epochs = (
            scaler, model, model_path, train_loader, val_loader, optimizer, scheduler, num_epochs
        )
            
    def train(self):
        self.model.train()
        
        mse_loss = nn.MSELoss(reduction='mean')
        
        best_val_rmse = None
        best_epoch = 0
        step = 0
        last_eval_step = 0
        eval_period = cfg.EVAL_SCHEDULE[0][1]    

        start = time.time()
        
        tbar = tqdm(range(self.num_epochs), total=self.num_epochs)
        for epoch in tbar:
            tbar.set_description(f'Epoch: {epoch}')
            val_rmse = None
            for batch_num, record in enumerate(self.train_loader):
                input_ids, attention_mask, target = record['input_ids'].to(cfg.DEVICE), record['attention_mask'].to(cfg.DEVICE), record['target'].to(cfg.DEVICE)
                
                self.optimizer.zero_grad()
                
                # Casts operations to mixed precision
                with torch.cuda.amp.autocast():
                    pred, _ = self.model(input_ids, attention_mask)
                    mse = mse_loss(pred.flatten(), target)
                    
                self.scaler.scale(mse).backward()
                self.scaler.step(self.optimizer)
                self.scaler.update()
                
                if self.scheduler:
                    self.scheduler.step()
                    
                if step >= last_eval_step + eval_period:
                    elapsed_seconds = time.time() - start
                    num_steps = step - last_eval_step
                    print(f"\n{num_steps} steps took {elapsed_seconds:0.3} seconds")
                    last_eval_step = step
                    
                    val_rmse = np.sqrt(eval_mse(self.model, self.val_loader))
                    print(f"Epoch: {epoch} batch_num: {batch_num}", f"val_rmse: {val_rmse:0.4} ", end='')
                    
                    eval_period = choose_eval_period(val_rmse)
                    best_epoch, best_val_rmse = serialize_best(best_val_rmse, best_epoch, val_rmse, epoch, self.model, self.model_path)
                    start = time.time()
                    
                    
                    
                # Finish early on condition
                if epoch > 0 and best_val_rmse > 0.6:
                    return best_val_rmse
                
                step += 1
        return best_val_rmse

kfold = KFold(n_splits=cfg.NUM_FOLDS, random_state=cfg.SEED, shuffle=True)
splits = list(kfold.split(train_df))

"""### Main Training"""

def train_fold(base_lr, last_lr, fold = 0):
    
    print(f'##### Using fold {fold}')
    
    model_path = cfg.MODEL_FOLDER + f"/{cfg.model_name.replace('/', '_')}_{fold + 1}/model_{fold + 1}.pth"
    
    set_random_seed(cfg.SEED + fold)
   
    
    #tokenizer = AutoTokenizer.from_pretrained(cfg.TOKENIZER_PATH)
    tokenizer = AutoTokenizer.from_pretrained(cfg.model_name)
    
    train_indices, val_indices = splits[fold]
    train_dataset = CommonLitDataset(train_df.loc[train_indices], tokenizer)    
    val_dataset = CommonLitDataset(train_df.loc[val_indices], tokenizer)
    
    train_loader = DataLoader(train_dataset, batch_size=cfg.BATCH_SIZE,
                              drop_last=False, shuffle=True, num_workers=cfg.NUM_WORKERS)    
    val_loader = DataLoader(val_dataset, batch_size=cfg.BATCH_SIZE,
                            drop_last=False, shuffle=False, num_workers=cfg.NUM_WORKERS)
    
    set_random_seed(cfg.SEED + fold)
    
    model = CommonLitModel().to(cfg.DEVICE)
    
    optimizer = create_optimizer(model, base_lr=base_lr, last_lr=last_lr)
    
    scheduler = get_cosine_schedule_with_warmup(optimizer,
                                                num_training_steps=cfg.NUM_EPOCHS * len(train_loader), 
                                                num_warmup_steps=50)
    
    scaler = torch.cuda.amp.GradScaler()
    
    trainer = Trainer(scaler, model, model_path, train_loader, val_loader, optimizer, scheduler = scheduler)
    rmse_val = trainer.train()
    tokenizer.save_pretrained(str(model_path.parent))
    
    return rmse_val

# Best results
# fold 0: {'base_lr': 4.214048623230046e-05, 'last_lr': 0.00098671139242345}. Best is trial 0 with value: 0.46920305490493774.
# fold 1: {'base_lr': 3.4594372607385946e-05, 'last_lr': 0.0005479134338105077}. Best is trial 0 with value: 0.447492390871048
# fold 2: {'base_lr': 1.777623134028703e-05, 'last_lr': 0.004132549020616918}. Best is trial 0 with value: 0.46756473183631897
# fold 3: {'base_lr': 3.933402254716856e-05, 'last_lr': 0.0018473297738188957}. Best is trial 11 with value: 0.4719877541065216
# fold 4: {'base_lr': 1.845975941382356e-05, 'last_lr': 0.0006309278277674714}. Best is trial 15 with value: 0.46920618414878845
# fold 5: {'base_lr': 4.430444436442592e-05, 'last_lr': 0.000289231685619846}. Best is trial 6 with value: 0.4629150927066803

# 'base_lr': 6.589032198953331e-05, 'last_lr': 0.00022464473383019027,
# {'base_lr': 6.589032198953331e-05, 'last_lr':0.00022464473383019027},
lr_list = [
    
    {'base_lr': 6.589032198953331e-05, 'last_lr':0.00022464473383019027},
    {'base_lr': 6.589032198953331e-05, 'last_lr':0.00022464473383019027},
    {'base_lr': 6.589032198953331e-05, 'last_lr':0.00022464473383019027},
    {'base_lr': 6.589032198953331e-05, 'last_lr':0.00022464473383019027},
    {'base_lr': 6.589032198953331e-05, 'last_lr':0.00022464473383019027},
    {'base_lr': 6.589032198953331e-05, 'last_lr':0.00022464473383019027}
    
]

# Commented out IPython magic to ensure Python compatibility.
# %%time
# 
# rmse_values = []
# for i in range(len(list(splits))):
#     fold = i
#     lrs = lr_list[fold]
#     rmse_val = train_fold(lrs['base_lr'], lrs['last_lr'], fold=fold)
#     print(f'Final RMSE: {rmse_val}')
#     rmse_values.append(rmse_val)

f'mean RMSE values: {np.mean(np.array(rmse_values))}'

"""### Verify the model"""

from sklearn.svm import SVR
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import mean_squared_error
from tqdm.notebook import tqdm

cfg.model_offset = 0
cfg.model_limit = 6
cfg.n_folds = 5
cfg.svm_kernels = ['rbf']
cfg.svm_c = 5

num_bins = int(np.ceil(np.log2(len(train_df))))
train_df['bins'] = pd.cut(train_df['target'], bins=num_bins, labels=False)
bins = train_df['bins'].values

# Commented out IPython magic to ensure Python compatibility.
# %%time
# 
# inference_models = []
# for i in range(1, cfg.NUM_FOLDS + 1):
#     print(f'Model {i}')
#     inference_model = CommonLitModel()
#     inference_model = inference_model.cuda()
#     inference_model.load_state_dict(torch.load(str(MODELS_PATHf"/{cfg.model_name.replace('/', '_')}_{i}/model_{i}.pth")))
#     inference_model.eval();
#     inference_models.append(inference_model)

from transformers import RobertaTokenizer

tokenizers = []
for i in range(1, cfg.NUM_FOLDS):
    tokenizer = RobertaTokenizer.from_pretrained(MODELS_PATH + f"/{cfg.model_name.replace('/', '_')}_{i}")
    tokenizers.append(tokenizer)

def get_cls_embeddings(dl, transformer_model):
    cls_embeddings = []
    with torch.no_grad():
        for input_features in tqdm(dl, total=len(dl)):
            output, context_vector = transformer_model(input_features['input_ids'].cuda(), input_features['attention_mask'].cuda())
#             cls_embeddings.extend(output['last_hidden_state'][:,0,:].detach().cpu().numpy())
            embedding_out = context_vector.detach().cpu().numpy()
            cls_embeddings.extend(embedding_out)
    return np.array(cls_embeddings)

def rmse_score(X, y):
    return np.sqrt(mean_squared_error(X, y))

def convert_to_list(t):
    return t.flatten().long()

class CommonLitDataset(nn.Module):
    def __init__(self, text, test_id, tokenizer, max_len=128):
        self.excerpt = text
        self.test_id = test_id
        self.max_len = max_len
        self.tokenizer = tokenizer
    
    def __getitem__(self,idx):
        encode = self.tokenizer(self.excerpt[idx],
                                return_tensors='pt',
                                max_length=self.max_len,
                                padding='max_length',
                                truncation=True)
        return {'input_ids': convert_to_list(encode['input_ids']),
                'attention_mask': convert_to_list(encode['attention_mask']),
                'id': self.test_id[idx]}
    
    def __len__(self):
        return len(self.excerpt)

def create_dl(df, tokenizer):
    text = df['excerpt'].values
    ids = df['id'].values
    ds = CommonLitDataset(text, ids, tokenizer, max_len=cfg.MAX_LEN)
    return DataLoader(ds, 
                      batch_size = cfg.BATCH_SIZE,
                      shuffle=False,
                      num_workers = 1,
                      pin_memory=True,
                      drop_last=False
                     )

train_df = pd.read_csv(DATA_PATH/'train-orig.csv')
test_df = pd.read_csv(DATA_PATH/'test.csv')
remove_unnecessary(train_df)

train_target_mean = train_df['target'].mean()
train_target_std = train_df['target'].std()
train_df['normalized_target'] = (train_df['target'] - train_target_mean) / train_target_std

# Commented out IPython magic to ensure Python compatibility.
# %%time
# 
# train_target = train_df['normalized_target'].values
# 
# def calc_mean(scores):
#     return np.mean(np.array(scores), axis=0)
# 
# final_scores = []
# final_rmse = []
# kernel_rmse_score_mean = []
# final_kernel_predictions_means = []
# for j, (inference_model, tokenizer) in enumerate(zip(inference_models, tokenizers)):
#     print('Model', j)
#     test_dl = create_dl(test_df, tokenizer)
#     train_dl = create_dl(train_df, tokenizer)
#     transformer_model = inference_model
#     transformer_model.cuda()
#     X = get_cls_embeddings(train_dl, transformer_model)
#     
#     y = train_target
#     X_test = get_cls_embeddings(test_dl, transformer_model)
#     
#     kfold = StratifiedKFold(n_splits=cfg.NUM_FOLDS)
#     scores = []
#     rmse_scores = []
#     kernel_predictions_means = []
#     for kernel in cfg.svm_kernels:
#         print('Kernel', kernel)
#         kernel_scores = []
#         kernel_rmse_scores = []
#         kernel_predictions = []
#         for k, (train_idx, valid_idx) in enumerate(kfold.split(X, bins)):
# 
#             print('Fold', k, train_idx.shape, valid_idx.shape)
#             model = SVR(C=cfg.svm_c, kernel=kernel, gamma='auto')
# 
#             X_train, y_train = X[train_idx], y[train_idx]
#             X_valid, y_valid = X[valid_idx], y[valid_idx]
#             model.fit(X_train, y_train)
#             prediction = model.predict(X_valid)
#             kernel_predictions.append(prediction)
#             kernel_rmse_scores.append(rmse_score(prediction, y_valid))
#             print('rmse_score', kernel_rmse_scores[k])
#             kernel_scores.append(model.predict(X_test))
#         kernel_predictions_means.append(np.array([np.mean(kp) for kp in kernel_predictions]).mean())
#         scores.append(calc_mean(kernel_scores))
#         kernel_rmse_score = calc_mean(kernel_rmse_scores)
#         kernel_rmse_score_mean.append(kernel_rmse_score)
#         rmse_scores.append(kernel_rmse_score)
#     final_kernel_predictions_means.append(kernel_predictions_means)
#     final_scores.append(calc_mean(scores))
#     final_rmse.append(calc_mean(rmse_scores))
# print('FINAL RMSE score', np.mean(np.array(final_rmse)))

final_kernel_predictions_means

# (train_df['target'] - cfg.train_target_mean) / cfg.train_target_std
final_scores_normalized = np.array(final_scores) * train_target_std + train_target_mean

kernel_rmse_score_mean_array = np.array(kernel_rmse_score_mean)
kernel_rmse_score_mean_sum = np.sum(kernel_rmse_score_mean_array)
prop_losses = kernel_rmse_score_mean_array / kernel_rmse_score_mean_sum
prop_losses_sum = (1 - prop_losses).sum()
weights = (1 - prop_losses) / prop_losses_sum
weights

def calc_mean(scores, weights=weights):
    return np.average(np.array(scores), weights=weights, axis=0)

target_mean = train_df['target'].mean()
final_scores_flat = calc_mean(final_scores_normalized).flatten()
final_scores_mean = final_scores_flat.mean()
target_mean, np.array(final_scores_normalized).mean()
# (-0.9579984513405823, -0.8029817438292849)

final_scores_flat

mean_diff = target_mean - final_scores_mean
mean_diff, mean_diff / len(final_scores)

sample_df['target'] = final_scores_flat + mean_diff
# sample_df['target'] = len(final_scores) / np.sum(1 / np.array(final_scores), axis=0) # harmonic mean
sample_df

"""### Prepare Packaging"""

cfg.model_name

BEST_MODEL_FOLDER = MODELS_PATH/cfg.model_name/'best'
!rm -rf {BEST_MODEL_FOLDER}
!mkdir -p {BEST_MODEL_FOLDER}

BEST_MODEL_FOLDER

cfg.NUM_FOLDS

bestmodels = [MODELS_PATH + f'/{cfg.model_name}_{i + 1}' for i in range(0, cfg.NUM_FOLDS)]

bestmodels

from shutil import copyfile

def normalize_name(path_name):
    return path_name.replace('', '')

for i, best_model in enumerate(bestmodels):
    print(f'Processing {i}th model')
    i = i + 1
    best_model_file = f'{best_model}/model_{i}.pth'
    if Path(best_model_file).exists():
        copyfile(best_model_file, f'{BEST_MODEL_FOLDER}/{i}_pytorch_model.bin')
        tokenizer_path = Path(BEST_MODEL_FOLDER/f'tokenizer-{i}')
        tokenizer_path.mkdir(parents=True, exist_ok=True)
        assert tokenizer_path.exists()

        tokenizer_json = Path(normalize_name(f'{MODELS_PATH/cfg.model_name}_{i}/tokenizer_config.json'))
        assert tokenizer_json.exists(), f'{tokenizer_json} does not exist'
        copyfile(tokenizer_json, tokenizer_path/'tokenizer.json')

        vocab_txt = Path(normalize_name(f'{MODELS_PATH/cfg.model_name}_{i}/vocab.json'))
        assert vocab_txt.exists(), f'{vocab_txt} does not exist'
        copyfile(vocab_txt, tokenizer_path/'vocab.json')

        merges = Path(normalize_name(f'{MODELS_PATH/cfg.model_name}_{i}/merges.txt'))
        assert merges.exists()
        copyfile(merges, tokenizer_path/'merges.txt')
    else:
        print(f'{best_model_file} is missing')

import shutil

shutil.make_archive(MODELS_PATH/cfg.model_name/'best_models', 'zip', BEST_MODEL_FOLDER)

!ls {MODELS_PATH/cfg.model_name}

!mv {MODELS_PATH}/{cfg.model_name}.yaml {MODELS_PATH/cfg.model_name}

transformer_model.transformer_model.save_pretrained(save_directory=f'{MODELS_PATH/cfg.model_name}/lm')

!du -h {MODELS_PATH/cfg.model_name}/*

shutil.make_archive(MODELS_PATH/cfg.model_name/'lm', 'zip', f'{MODELS_PATH/cfg.model_name}/lm')

!kaggle datasets init -p {MODELS_PATH/cfg.model_name}

dataset_json_path = Path(MODELS_PATH/cfg.model_name/'dataset-metadata.json')
assert dataset_json_path.exists()

!cat {str(dataset_json_path)}

with open(dataset_json_path, 'r') as f:
    dataset_json = f.read()
    dataset_json = dataset_json.replace('INSERT_TITLE_HERE', f'commonlit-{cfg.model_name}').replace('INSERT_SLUG_HERE', f'commonlit-{cfg.model_name}')
    print(dataset_json)
with(open(dataset_json_path, 'w')) as f:
    f.write(dataset_json)

!rm -rf {MODELS_PATH/cfg.model_name}/best
!rm -rf {MODELS_PATH/cfg.model_name}/lm

!kaggle datasets create -p {MODELS_PATH/cfg.model_name}

!kaggle datasets version -p {MODELS_PATH/cfg.model_name} -m "Version with merges.txt" -d

state_dict = torch.load(str(MODELS_PATH/f'distilroberta-0/checkpoint-105/pytorch_model.bin'))

loaded_model = CommonLitModel()

loaded_model.load_state_dict(state_dict)

