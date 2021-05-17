from torch import nn
import torch
import time
import DGCN
import numpy as np



class ModelTrainer():
    def __init__(self, model:nn.Module, loss:nn.Module, optimizer, lr:float, wd:float, n_epochs:int, dyn_kernel_config:dict, device:str):
        self.model = model
        self.model_name = self.model.__class__.__name__
        self.criterion = loss
        self.optimizer = optimizer(params=self.model.parameters(), lr=lr, weight_decay=wd)
        self.n_epochs = n_epochs
        self.dyn_kernel_config = dyn_kernel_config
        self.device = device

    def get_dyn_adj_list(self, adj:torch.Tensor):
        assert len(adj.shape) == 5
        _, seq_len, _, _, M = adj.shape

        dyn_adj_list = list()
        for m in range(M):
            t_list = list()
            for t in range(seq_len):
                adj_preprocessor = DGCN.DyAdj_Preprocessor(**self.dyn_kernel_config)
                t_list.append(adj_preprocessor.process(adj[:, t, :, :, m]).to(self.device))
            t_adj = torch.stack(t_list, dim=1)
            dyn_adj_list.append(t_adj)
        return dyn_adj_list


    def train(self, data_loader:dict, sta_adj_list:list, modes:list, model_dir:str, early_stopper=10):

        checkpoint = {'epoch':0, 'state_dict':self.model.state_dict()}
        val_loss = np.inf

        print('Training starts at: ', time.ctime())
        for epoch in range(1, self.n_epochs+1):

            running_loss = {mode:0.0 for mode in modes}
            for mode in modes:
                if mode == 'train':
                    self.model.train()
                else:
                    self.model.eval()

                step = 0
                for x, meta, P_dyn, y_true in data_loader[mode]:
                    with torch.set_grad_enabled(mode = mode=='train'):
                        if self.model_name == 'STIAM_Net':
                            dyn_adj_list = self.get_dyn_adj_list(P_dyn) if P_dyn is not None else None
                            y_pred = self.model(x_seq=x, meta=meta, dyn_adj_list=dyn_adj_list, sta_adj_list=sta_adj_list)

                        loss = self.criterion(y_pred, y_true)
                        if mode == 'train':
                            self.optimizer.zero_grad()
                            loss.backward()
                            self.optimizer.step()
                    running_loss[mode] += loss * y_true.shape[0]
                    step += y_true.shape[0]

                # epoch end
                if mode == 'validate':
                    if running_loss[mode]/step <= val_loss:
                        print(f'Epoch {epoch}, Val_loss drops from {val_loss:.5} to {running_loss[mode]/step:.5}. '
                              f'Update model checkpoint..')
                        val_loss = running_loss[mode]/step
                        checkpoint.update(epoch=epoch, state_dict=self.model.state_dict())
                        torch.save(checkpoint, model_dir + f'/{self.model_name}_best_model.pkl')
                        early_stopper = 10
                    else:
                        print(f'Epoch {epoch}, Val_loss does not improve from {val_loss:.5}.')
                        early_stopper -= 1
                        if early_stopper == 0:
                            print(f'Early stopping at epoch {epoch}..')
                            return

        print('Training ends at: ', time.ctime())
        torch.save(checkpoint, model_dir + f'/{self.model_name}_best_model.pkl')

        return


    def test(self, data_loader:dict, sta_adj_list:list, modes:list, model_dir:str, data_class):

        saved_checkpoint = torch.load(model_dir + f'/{self.model_name}_best_model.pkl')
        self.model.load_state_dict(saved_checkpoint['state_dict'])
        self.model.eval()

        print('Testing starts at: ', time.ctime())
        running_loss = {mode: 0.0 for mode in modes}
        for mode in modes:
            ground_truth, prediction = list(), list()
            for x, meta, P_dyn, y_true in data_loader[mode]:
                if self.model_name == 'STIAM_Net':
                    dyn_adj_list = self.get_dyn_adj_list(P_dyn) if P_dyn is not None else None
                    y_pred = self.model(x_seq=x, meta=meta, dyn_adj_list=dyn_adj_list, sta_adj_list=sta_adj_list)

                ground_truth.append(y_true.cpu().detach().numpy())
                prediction.append(y_pred.cpu().detach().numpy())

                loss = self.criterion(y_pred, y_true)
                running_loss[mode] += loss * y_true.shape[0]

            ground_truth = data_class.std_denormalize(np.concatenate(ground_truth, axis=0))
            prediction = data_class.std_denormalize(np.concatenate(prediction, axis=0))

            print(f'{mode} true MSE: ', self.MSE(prediction, ground_truth))
            print(f'{mode} true RMSE: ', self.RMSE(prediction, ground_truth))
            print(f'{mode} true MAE: ', self.MAE(prediction, ground_truth))
            print(f'{mode} true MAPE: ', self.MAPE(prediction, ground_truth) * 100, '%')
        print('Testing ends at: ', time.ctime())

        return

    @staticmethod
    def MSE(y_pred:np.array, y_true:np.array):
        return np.mean(np.square(y_pred - y_true))
    @staticmethod
    def RMSE(y_pred:np.array, y_true:np.array):
        return np.sqrt(np.mean(np.square(y_pred - y_true)))
    @staticmethod
    def MAE(y_pred:np.array, y_true:np.array):
        return np.mean(np.abs(y_pred - y_true))
    @staticmethod
    def MAPE(y_pred:np.array, y_true:np.array, epsilon=1e-0):   # zero division
        return np.mean(np.abs(y_pred - y_true) / (y_true + epsilon))
    @staticmethod
    def PCC(y_pred:np.array, y_true:np.array):
        return np.corrcoef(y_pred.flatten(), y_true.flatten())[0,1]
