import os
import argparse
import torch
import torchvision
import torchvision.transforms as transforms
import torch.utils.data
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import numpy as np
from torch.autograd import Variable
import math

from collections import defaultdict
import time
import pdb
#torch.manual_seed(0)
#np.random.seed(0)

os.environ["CUDA_VISIBLE_DEVICES"] = '0'
device = torch.device('cuda') 


#device = torch.device('cpu')
def leaky_relu_derivative(x, slope):  
    slope1 = torch.ones_like(x).detach()
    slope2 = torch.ones_like(x).detach() * slope
    return torch.where(x > 0, slope1, slope2).detach()
    

#DO NOT FORGET ACTNORM!!!
class BasicBlockA(nn.Module):
#Input_dim should be 1(grey scale image) or 3(RGB image), or other dimension if use SpaceToDepth
    def __init__(self, latent_dim, stride=1, input_dim=3, kernel=3):
        super(BasicBlockA, self).__init__()
        self.input_dim = input_dim
        self.latent_dim = latent_dim               
        self.kernel = kernel
        self.weight_list1 = nn.ParameterList()
        self.center_list = nn.ParameterList()
        self.bias_list1 = nn.ParameterList()
        self.res = nn.Parameter(torch.ones(1))
        
        for i in range(latent_dim):
            weight = torch.Tensor(input_dim, input_dim, kernel, kernel)
            bias = torch.Tensor(input_dim) 
            nn.init.xavier_normal_(weight)
            nn.init.normal_(bias)
            self.weight_list1.append(nn.Parameter(weight))
            self.bias_list1.append(nn.Parameter(bias))
            center = np.random.randn(input_dim, input_dim, kernel, kernel)
            self.center_list.append(nn.Parameter(torch.Tensor(center)))
            

        self.weight_list2 = nn.ParameterList()
        self.bias_list2 = nn.ParameterList()
        for i in range(latent_dim**2):
            weight = torch.Tensor(input_dim, input_dim, kernel, kernel)
            nn.init.xavier_normal_(weight)
            bias = torch.Tensor(input_dim)
            nn.init.normal_(bias)
            self.weight_list2.append(nn.Parameter(weight))
            self.bias_list2.append(nn.Parameter(bias))

        # Define masks
        kernel_mid_y, kernel_mid_x = kernel//2, kernel//2
        # zero in the middle(technically not middle, depending on channels), one elsewhere
        # used to mask out the diagonal element
        self.mask0 = np.ones((input_dim, input_dim, kernel, kernel), dtype=np.float32)
  
        # 1 in the middle, zero elsewhere, used for center mask to zero out the non-diagonal element
        self.mask1 = np.zeros((input_dim, input_dim, kernel, kernel), dtype=np.float32)

        # Mask out the element above diagonal
        self.mask = np.ones((input_dim, input_dim, kernel, kernel), dtype=np.float32)        

        #For RGB ONLY:i=0:Red channel;i=1:Green channel;i=2:Blue channel 
        for i in range(input_dim):
            self.mask0[i,i,kernel_mid_y,kernel_mid_x] = 0.0
            self.mask1[i,i,kernel_mid_y,kernel_mid_x] = 1.0
            self.mask[i,:, kernel_mid_y+1:, :] = 0.0
            # For the current and previous color channels, including the current color
            self.mask[i,:i+1, kernel_mid_y, kernel_mid_x+1:] = 0.0
            # For the latter color channels, not including the current color
            self.mask[i, i+1:, kernel_mid_y, kernel_mid_x:] = 0.0

        self.mask0 = torch.Tensor(self.mask0).to(device)
        self.mask1 = torch.Tensor(self.mask1).to(device)
        self.mask = torch.Tensor(self.mask).to(device)



    def forward(self, x):
        log_det = x[1]
        x = x[0]
        residual = x        
        latent1 = []  
        self.diag = torch.zeros(x.size(), device= device)
        
        for i in range(self.latent_dim):
            latent_output = F.conv2d(x, (self.weight_list1[i]*self.mask0 + self.center_list[i]*self.mask1)*self.mask, bias=self.bias_list1[i], padding=1)

            for j in range(self.input_dim):
                self.diag[:,j,:,:] = self.diag[:,j,:,:] + ((self.center_list[i][j, j, self.kernel//2, self.kernel//2])**2) * leaky_relu_derivative(latent_output[:,j,:,:], 0.1)                
            
            latent_output = F.leaky_relu(latent_output,negative_slope=0.1)
            latent1.append(latent_output)
            
        latent2 = []
        # remove for loops. Whether can do everything using one convolution. torch.einsum()
        for i in range(self.latent_dim):
            for j in range(self.latent_dim):
                latent_output = F.conv2d(latent1[j],\
                (self.weight_list2[i*self.latent_dim+j]*self.mask0 + self.center_list[j]*self.mask1)*self.mask, bias=self.bias_list2[i*self.latent_dim+j], padding=1)
                latent2.append(latent_output)
        
            
        output = torch.stack(latent2, dim=0)
        output = output.sum(dim=0)/len(latent2)
        mask_res = (self.res>0).float().to(device)
        output = output + self.res * mask_res * residual  
        self.diag = self.diag + self.res * mask_res
        log_det = log_det + torch.sum(torch.log(self.diag))
        return output, log_det


class BasicBlockB(nn.Module):
#input_dim should be 1(grey scale image) or 3(RGB image)
    def __init__(self, latent_dim, stride=1, input_dim=3, kernel=3):
        super(BasicBlockB, self).__init__()
        self.input_dim = input_dim
        self.latent_dim = latent_dim               
        self.kernel = kernel
        self.weight_list1 = nn.ParameterList()
        self.center_list = nn.ParameterList()
        self.bias_list1 = nn.ParameterList()
        self.res = nn.Parameter(torch.ones(1))
        
        for i in range(latent_dim):
            weight = torch.Tensor(input_dim, input_dim, kernel, kernel)
            bias = torch.Tensor(input_dim)
            nn.init.xavier_normal_(weight)
            nn.init.normal_(bias)
            self.weight_list1.append(nn.Parameter(weight))
            self.bias_list1.append(nn.Parameter(bias))
            center = np.random.randn(input_dim, input_dim, kernel, kernel)
            self.center_list.append(nn.Parameter(torch.Tensor(center)))

        self.weight_list2 = nn.ParameterList()
        self.bias_list2 = nn.ParameterList()
        for i in range(latent_dim**2):
            weight = torch.Tensor(input_dim, input_dim, kernel, kernel)
            nn.init.xavier_normal_(weight)
            bias = torch.Tensor(input_dim)
            nn.init.normal_(bias)
            self.weight_list2.append(nn.Parameter(weight))
            self.bias_list2.append(nn.Parameter(bias))

        # Define masks
        kernel_mid_y, kernel_mid_x = kernel//2, kernel//2
        # zero in the middle(technically not middle, depending on channels), one elsewhere
        # used to mask out the diagonal element
        self.mask0 = np.ones((input_dim, input_dim, kernel, kernel), dtype=np.float32)
  
        # 1 in the middle, zero elsewhere, used for center mask to zero out the non-diagonal element
        self.mask1 = np.zeros((input_dim, input_dim, kernel, kernel), dtype=np.float32)

        # Mask out the element above diagonal
        self.mask = np.ones((input_dim, input_dim, kernel, kernel), dtype=np.float32)        

        #i=0:Red channel;i=1:Green channel;i=2:Blue channel
        for i in range(input_dim):
            self.mask0[i,i,kernel_mid_y,kernel_mid_x] = 0.0
            self.mask1[i,i,kernel_mid_y,kernel_mid_x] = 1.0
            self.mask[i,:, :kernel_mid_y, :] = 0.0
            # For the current and latter color channels, including the current color
            self.mask[i,i:, kernel_mid_y, :kernel_mid_x] = 0.0
            # For the previous color channels, not including the current color
            self.mask[i,:i, kernel_mid_y, :kernel_mid_x+1] = 0.0

        self.mask0 = torch.Tensor(self.mask0).to(device)
        self.mask1 = torch.Tensor(self.mask1).to(device)
        self.mask = torch.Tensor(self.mask).to(device)


    def forward(self, x):
        log_det = x[1]
        x = x[0]
        residual = x        
        latent1 = []  
        #changed use to be zeros
        self.diag = torch.zeros(x.size(), device= device)
        
        for i in range(self.latent_dim):
            latent_output = F.conv2d(x, (self.weight_list1[i]*self.mask0 + self.center_list[i]*self.mask1)*self.mask, bias=self.bias_list1[i], padding=1)
          
            
            for j in range(self.input_dim):
                #pdb.set_trace()
                self.diag[:,j,:,:] = self.diag[:,j,:,:] + ((self.center_list[i][j, j, self.kernel//2, self.kernel//2])**2) * leaky_relu_derivative(latent_output[:,j,:,:], 0.1)                
            
            latent_output = F.leaky_relu(latent_output,negative_slope=0.1)
            latent1.append(latent_output)
      
        latent2 = []
        # remove for loops. Whether can do everything using one convolution. torch.einsum()
        for i in range(self.latent_dim):
            for j in range(self.latent_dim):
                latent_output = F.conv2d(latent1[j],\
                (self.weight_list2[i*self.latent_dim+j]*self.mask0 + self.center_list[j]*self.mask1)*self.mask, bias=self.bias_list2[i*self.latent_dim+j], padding=1)
                latent2.append(latent_output)
         
        output = torch.stack(latent2, dim=0)
        output = output.sum(dim=0)/len(latent2)
        mask_res = (self.res>0).float().to(device)
        output = output + self.res * mask_res * residual  
        self.diag = self.diag + self.res * mask_res
        log_det = log_det + torch.sum(torch.log(self.diag))
        return output, log_det


class SpaceToDepth(nn.Module):
    def __init__(self, block_size):
        super(SpaceToDepth, self).__init__()
        self.block_size = block_size
        self.block_size_sq = block_size*block_size

    def forward(self, input):
        output = input.permute(0, 2, 3, 1)
        (batch_size, s_height, s_width, s_depth) = output.size()
        d_depth = s_depth * self.block_size_sq
        d_width = int(s_width / self.block_size)
        d_height = int(s_height / self.block_size)
        t_1 = output.split(self.block_size, 2)
        stack = [t_t.reshape(batch_size, d_height, d_depth) for t_t in t_1]
        output = torch.stack(stack, 1)
        output = output.permute(0, 2, 1, 3)
        output = output.permute(0, 3, 1, 2)
        return output
    
    
class Net(nn.Module):
    #layers latent_dim at each layer
    def __init__(self, blockA, blockB, layer_size, latent_size, image_size=32, input_channel=3, num_classes=10):
        self.inplanes = input_channel
        super(Net, self).__init__()
        channel = input_channel
        self.increase_dim = SpaceToDepth(4)
        self.layer1 = self._make_layer(layer_size[0], blockA, blockB, latent_size[0], channel)
        #channel *= 4 * 4
        self.layer2 = self._make_layer(layer_size[1], blockA, blockB, latent_size[0], channel)
        self.layer3 = self._make_layer(layer_size[2], blockA, blockB, latent_size[0], channel)


    def _make_layer(self, block_num, blockA, blockB, latent_dim, input_dim, stride=1):
        layers = []
        for i in range(0, 1):
            layers.append(blockA(latent_dim,input_dim=input_dim))
            layers.append(blockB(latent_dim,input_dim=input_dim))
        return nn.Sequential(*layers)

    def forward(self, x):
        log_det = torch.zeros([1], device = device)
        x, log_det = self.layer1([x, log_det])
        #x = self.increase_dim(x)
        x, log_det = self.layer2([x, log_det])
        #x, log_det = self.layer3([x, log_det])
        
        x = x.view(x.shape[0], -1)
        return x, log_det 

 

