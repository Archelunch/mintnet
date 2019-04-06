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
import pdb

torch.manual_seed(0)
np.random.seed(0)

os.environ["CUDA_VISIBLE_DEVICES"] = '0'
device = torch.device('cuda')


class GroupNorm(nn.Module):
    def __init__(self, num_inputs):
        super(GroupNorm, self).__init__()
        self.weight = nn.Parameter(torch.ones(num_inputs)).to(device)
        self.bias = nn.Parameter(torch.zeros(num_inputs)).to(device)
        self.initialized = False

    def forward(self, inputs):
        
        if self.initialized == False:
            #pdb.set_trace()
            self.weight.data.copy_((inputs.reshape((inputs.shape[0], -1))).std(1))
            self.bias.data.copy_((inputs.reshape((inputs.shape[0], -1))).mean(1))
            self.initialized = True
        #pdb.set_trace()
        self.weight = self.weight.repeat(1,1,1,1).transpose(0,-1)
        self.bias = self.bias.repeat(1,1,1,1).transpose(0,-1)

        return (inputs - self.bias) * self.weight
        
# DO NOT FORGET BATCH_NORM!!!
class BasicBlockA(nn.Module):
    # Input_dim should be 1(grey scale image) or 3(RGB image), or other dimension if use SpaceToDepth
    def __init__(self, config, latent_dim, stride=1, input_dim=3, kernel=3):
        super(BasicBlockA, self).__init__()
        self.input_dim = input_dim
        self.latent_dim = latent_dim
        self.kernel = kernel
        self.weight_list1 = nn.ParameterList()
        self.center_list1 = nn.ParameterList()
        self.bias_list1 = nn.ParameterList()
        self.res = nn.Parameter(torch.ones(1))

        for i in range(latent_dim):
            weight = torch.zeros(input_dim, input_dim, kernel, kernel, device=config.device)
            bias = torch.zeros(input_dim, device=config.device)
            nn.init.xavier_normal_(weight)
            nn.init.normal_(bias)
            self.weight_list1.append(nn.Parameter(weight))
            self.bias_list1.append(nn.Parameter(bias))
            center = torch.randn(input_dim, input_dim, kernel, kernel, device=config.device)
            self.center_list1.append(nn.Parameter(center))

        self.center_list2 = nn.ParameterList()
        self.weight_list2 = nn.ParameterList()
        self.bias_list2 = nn.ParameterList()
        for i in range(latent_dim):
            center = torch.randn(input_dim, input_dim, kernel, kernel, device=config.device)
            weight = torch.zeros_like(center)
            nn.init.xavier_normal_(weight)
            bias = torch.zeros(input_dim, device=config.device)
            nn.init.normal_(bias)
            self.center_list2.append(nn.Parameter(center))
            self.weight_list2.append(nn.Parameter(weight))
            self.bias_list2.append(nn.Parameter(bias))

        # Define masks
        kernel_mid_y, kernel_mid_x = kernel // 2, kernel // 2
        # zero in the middle(technically not middle, depending on channels), one elsewhere
        # used to mask out the diagonal element
        self.mask0 = np.ones((input_dim, input_dim, kernel, kernel), dtype=np.float32)

        # 1 in the middle, zero elsewhere, used for center mask to zero out the non-diagonal element
        self.mask1 = np.zeros((input_dim, input_dim, kernel, kernel), dtype=np.float32)

        # Mask out the element above diagonal
        self.mask = np.ones((input_dim, input_dim, kernel, kernel), dtype=np.float32)

        # For RGB ONLY:i=0:Red channel;i=1:Green channel;i=2:Blue channel
        for i in range(input_dim):
            self.mask0[i, i, kernel_mid_y, kernel_mid_x] = 0.0
            self.mask1[i, i, kernel_mid_y, kernel_mid_x] = 1.0
            self.mask[i, :, kernel_mid_y + 1:, :] = 0.0
            # For the current and previous color channels, including the current color
            self.mask[i, :i + 1, kernel_mid_y, kernel_mid_x + 1:] = 0.0
            # For the latter color channels, not including the current color
            self.mask[i, i + 1:, kernel_mid_y, kernel_mid_x:] = 0.0

        self.mask0 = torch.tensor(self.mask0, device=config.device)
        self.mask1 = torch.tensor(self.mask1, device=config.device)
        self.mask = torch.tensor(self.mask, device=config.device)

    def forward(self, x):
        residual = x
        latent1 = []

        for i in range(self.latent_dim):
            latent_output = F.conv2d(x, (self.weight_list1[i] * self.mask0 + torch.nn.functional.softplus(
                self.center_list1[i]) * self.mask1) * self.mask, bias=self.bias_list1[i], padding=1)
            
            latent_output = F.elu(latent_output, alpha=1)
            latent1.append(latent_output)

        latent2 = []
        for i in range(self.latent_dim):
            latent_output = F.conv2d(latent1[i], \
                                     (self.weight_list2[i] * self.mask0 + torch.nn.functional.softplus(
                                         self.center_list2[i]) * self.mask1) * self.mask, bias=self.bias_list2[i],
                                     padding=1)
            latent2.append(latent_output)

        output = torch.stack(latent2, dim=0)
        output = output.sum(dim=0) / len(latent2)
        mask_res = (self.res > 0).float().to(x.device)
        # MIGHT NEED TO ADD EPSILON TO self.res * mask_res
        output = output + self.res * mask_res * residual
        
        return output


class BasicBlockB(nn.Module):
    # input_dim should be 1(grey scale image) or 3(RGB image)
    def __init__(self, config, latent_dim, stride=1, input_dim=3, kernel=3):
        super(BasicBlockB, self).__init__()
        self.input_dim = input_dim
        self.latent_dim = latent_dim
        self.kernel = kernel
        self.weight_list1 = nn.ParameterList()
        self.center_list1 = nn.ParameterList()
        self.bias_list1 = nn.ParameterList()
        self.res = nn.Parameter(torch.ones(1))

        for i in range(latent_dim):
            weight = torch.zeros(input_dim, input_dim, kernel, kernel, device=config.device)
            bias = torch.zeros(input_dim, device=config.device)
            nn.init.xavier_normal_(weight)
            nn.init.normal_(bias)
            self.weight_list1.append(nn.Parameter(weight))
            self.bias_list1.append(nn.Parameter(bias))
            center = torch.randn(input_dim, input_dim, kernel, kernel, device=config.device)
            self.center_list1.append(nn.Parameter(center))

        self.center_list2 = nn.ParameterList()
        self.weight_list2 = nn.ParameterList()
        self.bias_list2 = nn.ParameterList()
        for i in range(latent_dim):
            center = torch.randn(input_dim, input_dim, kernel, kernel, device=config.device)
            weight = torch.zeros_like(center)
            nn.init.xavier_normal_(weight)
            bias = torch.zeros(input_dim, device=config.device)
            nn.init.normal_(bias)
            self.center_list2.append(nn.Parameter(center))
            self.weight_list2.append(nn.Parameter(weight))
            self.bias_list2.append(nn.Parameter(bias))

        # Define masks
        kernel_mid_y, kernel_mid_x = kernel // 2, kernel // 2
        # zero in the middle(technically not middle, depending on channels), one elsewhere
        # used to mask out the diagonal element
        self.mask0 = np.ones((input_dim, input_dim, kernel, kernel), dtype=np.float32)

        # 1 in the middle, zero elsewhere, used for center mask to zero out the non-diagonal element
        self.mask1 = np.zeros((input_dim, input_dim, kernel, kernel), dtype=np.float32)

        # Mask out the element above diagonal
        self.mask = np.ones((input_dim, input_dim, kernel, kernel), dtype=np.float32)

        # i=0:Red channel;i=1:Green channel;i=2:Blue channel
        for i in range(input_dim):
            self.mask0[i, i, kernel_mid_y, kernel_mid_x] = 0.0
            self.mask1[i, i, kernel_mid_y, kernel_mid_x] = 1.0
            self.mask[i, :, :kernel_mid_y, :] = 0.0
            # For the current and latter color channels, including the current color
            self.mask[i, i:, kernel_mid_y, :kernel_mid_x] = 0.0
            # For the previous color channels, not including the current color
            self.mask[i, :i, kernel_mid_y, :kernel_mid_x + 1] = 0.0

        self.mask0 = torch.tensor(self.mask0, device=config.device)
        self.mask1 = torch.tensor(self.mask1, device=config.device)
        self.mask = torch.tensor(self.mask, device=config.device)

    def forward(self, x):
        residual = x
        latent1 = []

        for i in range(self.latent_dim):
            latent_output = F.conv2d(x, (self.weight_list1[i] * self.mask0 + torch.nn.functional.softplus(
                self.center_list1[i]) * self.mask1) * self.mask, bias=self.bias_list1[i], padding=1)

            latent_output = F.elu(latent_output, alpha=1)
            latent1.append(latent_output)

        latent2 = []
        for i in range(self.latent_dim):
            latent_output = F.conv2d(latent1[i], \
                                     (self.weight_list2[i] * self.mask0 + torch.nn.functional.softplus(
                                         self.center_list2[i]) * self.mask1) * self.mask, bias=self.bias_list2[i],
                                     padding=1)
            latent2.append(latent_output)

        output = torch.stack(latent2, dim=0)
        output = output.sum(dim=0) / len(latent2)
        mask_res = (self.res > 0).float().to(x.device)
        output = output + self.res * mask_res * residual

        # need to add act_norm

        return output




class DepthToSpace(nn.Module):
    def __init__(self, block_size):
        super(DepthToSpace, self).__init__()
        self.block_size = block_size
        self.block_size_sq = block_size * block_size

    def forward(self, input):
        output = input.permute(0, 2, 3, 1)
        (batch_size, d_height, d_width, d_depth) = output.size()
        s_depth = int(d_depth / self.block_size_sq)
        s_width = int(d_width * self.block_size)
        s_height = int(d_height * self.block_size)
        t_1 = output.reshape(batch_size, d_height, d_width, self.block_size_sq, s_depth)
        spl = t_1.split(self.block_size, 3)
        stack = [t_t.reshape(batch_size, d_height, s_width, s_depth) for t_t in spl]
        output = torch.stack(stack, 0).transpose(0, 1).permute(0, 2, 1, 3, 4).reshape(batch_size, s_height, s_width,
                                                                                      s_depth)
        output = output.permute(0, 3, 1, 2)
        return output


class SpaceToDepth(nn.Module):
    def __init__(self, block_size):
        super(SpaceToDepth, self).__init__()
        self.block_size = block_size
        self.block_size_sq = block_size * block_size

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
    # layers latent_dim at each layer
    def __init__(self, config):
        super().__init__()
        self.config = config
        blockA = BasicBlockA
        blockB = BasicBlockB
        self.num_classes = config.data.num_classes
        self.inplanes = channel = config.data.channels
        self.increase_dim = SpaceToDepth(4)
        self.image_size = config.data.image_size
        layer_size = config.model.layer_size
        latent_size = config.model.latent_size
        # self.increase_dim = SpaceToDepth(2)
        self.layer1 = self._make_layer(layer_size[0], blockA, blockB, latent_size[0], channel)
        # channel *= 2 * 2
        # channel *= 4 * 4
        self.layer2 = self._make_layer(layer_size[1], blockA, blockB, latent_size[1], channel)
        self.layer3 = self._make_layer(layer_size[2], blockA, blockB, latent_size[2], channel)  
        self.fc = nn.Linear(self.inplanes * self.image_size * self.image_size, self.num_classes)

    def _make_layer(self, block_num, blockA, blockB, latent_dim, input_dim, stride=1):
        layers = []
        for i in range(0, block_num):
            layers.append(blockA(self.config, latent_dim, input_dim=input_dim))
            layers.append(blockB(self.config, latent_dim, input_dim=input_dim))
        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.layer1(x)
        #x = self.increase_dim(x)
        #x = GroupNorm(x.shape[0])(x)
        x = self.layer2(x)
        #x = GroupNorm(x.shape[0])(x)
        #pdb.set_trace()
        x = self.layer3(x)
        #x = self.layer4(x)
        
        x = x.view(x.shape[0], -1)
        x = self.fc(x)
        return F.log_softmax(x, dim=1)
