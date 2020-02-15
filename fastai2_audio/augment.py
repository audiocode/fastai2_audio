# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/01_augment.ipynb (unless otherwise specified).

__all__ = ['RemoveSilence', 'Resample', 'CropSignal', 'shift_signal', 'SignalShifter', 'AddNoise', 'ChangeVolume',
           'SignalCutout', 'SignalLoss', 'DownmixMono', 'CropTime', 'MaskFreq', 'MaskTime', 'SGRoll', 'Delta',
           'TfmResize', 'AudioBlock']

# Cell
from fastai2.torch_basics import *
from fastai2.data.all import *
from .core import *
from fastai2.vision.augment import *

# Cell
import torch.nn
from torch import stack, zeros_like as t0, ones_like as t1
from torch.distributions.bernoulli import Bernoulli
from librosa.effects import split
from dataclasses import asdict
from scipy.signal import resample_poly

from scipy.ndimage.interpolation import shift
import librosa
import colorednoise as cn

# Cell
mk_class('RemoveType', **{o:o.lower() for o in ['Trim', 'All', 'Split']},
         doc="All methods of removing silence as attributes to get tab-completion and typo-proofing")

# Cell
def _merge_splits(splits, pad):
    clip_end = splits[-1][1]
    merged = []
    i=0
    while i < len(splits):
        start = splits[i][0]
        while splits[i][1] < clip_end and splits[i][1] + pad >= splits[i+1][0] - pad:
            i += 1
        end = splits[i][1]
        merged.append(np.array([max(start-pad, 0), min(end+pad, clip_end)]))
        i+=1
    return np.stack(merged)

def RemoveSilence(remove_type=RemoveType.Trim, threshold=20, pad_ms=20):
    def _inner(ai:AudioTensor)->AudioTensor:
        '''Split signal at points of silence greater than 2*pad_ms '''
        if remove_type is None: return ai
        padding = int(pad_ms/1000*ai.sr)
        if(padding > ai.nsamples): return ai
        splits = split(ai.numpy(), top_db=threshold, hop_length=padding)
        if remove_type == "split":
            sig =  [ai[:,(max(a-padding,0)):(min(b+padding,ai.nsamples))]
                    for (a, b) in _merge_splits(splits, padding)]
        elif remove_type == "trim":
            sig = [ai[:,(max(splits[0, 0]-padding,0)):splits[-1, -1]+padding]]
        elif remove_type == "all":
            sig = [torch.cat([ai[:,(max(a-padding,0)):(min(b+padding,ai.nsamples))]
                              for (a, b) in _merge_splits(splits, padding)], dim=1)]
        else:
            raise ValueError(f"Valid options for silence removal are None, 'split', 'trim', 'all' not '{remove_type}'.")
        ai.data = torch.cat(sig, dim=-1)
        return ai
    return _inner

# Cell
def Resample(sr_new):
    def _inner(ai:AudioTensor)->AudioTensor:
        '''Resample using faster polyphase technique and avoiding FFT computation'''
        if(ai.sr == sr_new): return ai
        sig_np = ai.numpy()
        sr_gcd = math.gcd(ai.sr, sr_new)
        resampled = resample_poly(sig_np, int(sr_new/sr_gcd), int(ai.sr/sr_gcd), axis=-1)
        ai.data = torch.from_numpy(resampled.astype(np.float32))
        ai.sr = sr_new
        return ai
    return _inner

# Cell
mk_class('AudioPadType', **{o:o.lower() for o in ['Zeros', 'Zeros_After', 'Repeat']},
         doc="All methods of padding audio as attributes to get tab-completion and typo-proofing")

# Cell
def CropSignal(duration, pad_mode=AudioPadType.Zeros):
    def _inner(ai: AudioTensor)->AudioTensor:
        '''Crops signal to be length specified in ms by duration, padding if needed'''
        sig = ai.data
        orig_samples = ai.nsamples
        crop_samples = int((duration/1000)*ai.sr)
        if orig_samples == crop_samples: return ai
        elif orig_samples < crop_samples:
            ai.data = _tfm_pad_signal(sig, crop_samples, pad_mode=pad_mode)
        else:
            crop_start = random.randint(0, int(orig_samples-crop_samples))
            ai.data = sig[:,crop_start:crop_start+crop_samples]
        return ai
    return _inner

# Cell
def _tfm_pad_signal(sig, width, pad_mode=AudioPadType.Zeros):
    '''Pad spectrogram to specified width, using specified pad mode'''
    c,x = sig.shape
    pad_m = pad_mode.lower()
    if pad_m in ["zeros", "zeros_after"]:
        zeros_front = random.randint(0, width-x) if pad_m == "zeros" else 0
        pad_front = torch.zeros((c, zeros_front))
        pad_back = torch.zeros((c, width-x-zeros_front))
        return torch.cat((pad_front, sig, pad_back), 1)
    elif pad_m == "repeat":
        repeats = width//x + 1
        return sig.repeat(1,repeats)[:,:width]
    else:
        raise ValueError(f"pad_mode {pad_m} not currently supported, only 'zeros', 'zeros_after', or 'repeat'")

# Cell
def _shift(sig, s):
    if s == 0: return sig
    out = torch.zeros_like(sig)
    if  s < 0: out[...,:s] = sig[...,-s:]
    else: out[...,s:] = sig[...,:-s]
    return out

def shift_signal(t:torch.Tensor, shift, roll):
    #refactor 2nd half of this statement to just take and roll the final axis
    if roll: t.data = torch.from_numpy(np.roll(t.numpy(), shift, axis=-1))
    else   : t.data = _shift(t, shift)
    return t

# Cell
class SignalShifter(RandTransform):
    def __init__(self, p=0.5, max_pct= 0.2, max_time=None, direction=0, roll=False):
        if direction not in [-1, 0, 1]: raise ValueError("Direction must be -1(left) 0(bidirectional) or 1(right)")
        store_attr(self, "max_pct,max_time,direction,roll")
        super().__init__(p=p, as_item=True)

    def before_call(self, b, split_idx):
        super().before_call(b, split_idx)
        self.shift_factor = random.uniform(-1, 1)
        if self.direction != 0: self.shift_factor = self.direction*abs(self.shift_factor)

    def encodes(self, ai:AudioTensor):
        if self.max_time is None: s = self.shift_factor*self.max_pct*ai.nsamples
        else:                     s = self.shift_factor*self.max_time*ai.sr
        ai.data = shift_signal(ai.data, int(s), self.roll)
        return ai

    def encodes(self, sg:AudioSpectrogram):
        if self.max_time is None: s = self.shift_factor*self.max_pct*sg.width
        else:                     s = self.shift_factor*self.max_time*sg.sr
        return shift_signal(sg, int(s), self.roll)

# Cell
mk_class('NoiseColor', **{o:i-2 for i,o in enumerate(['Violet', 'Blue', 'White', 'Pink', 'Brown'])},
         doc="All possible colors of noise as attributes to get tab-completion and typo-proofing")

# Cell
def AddNoise(noise_level=0.05, color=NoiseColor.White):
    def _inner(ai: AudioTensor)->AudioTensor:
        # if it's white noise, implement our own for speed
        if color==0: noise = torch.randn_like(ai.data)
        else:        noise = torch.from_numpy(cn.powerlaw_psd_gaussian(exponent=color, size=ai.nsamples)).float()
        scaled_noise = noise * ai.data.abs().mean() * noise_level
        ai.data += scaled_noise
        return ai
    return _inner

# Cell
@patch
def apply_gain(ai:AudioTensor, gain):
    ai.data *= gain
    return ai

# Cell
class ChangeVolume(RandTransform):
    def __init__(self, p=0.5, lower=0.5, upper=1.5):
        self.lower, self.upper = lower, upper
        super().__init__(p=p, as_item=True)

    def before_call(self, b, split_idx):
        super().before_call(b, split_idx)
        self.gain = random.uniform(self.lower, self.upper)

    def encodes(self, ai:AudioTensor): return apply_gain(ai, self.gain)

# Cell
@patch
def cutout(ai:AudioTensor, cut_pct):
    mask = torch.zeros(int(ai.nsamples*cut_pct))
    mask_start = random.randint(0,ai.nsamples-len(mask))
    ai.data[:,mask_start:mask_start+len(mask)] = mask
    return ai

# @patch
# def cutout(sg:AudioSpectrogram, cut_pct):

# Cell
class SignalCutout(RandTransform):
    def __init__(self, p=0.5, max_cut_pct=0.15):
        self.max_cut_pct = max_cut_pct
        super().__init__(p=p, as_item=True)

    def before_call(self, b, split_idx):
        super().before_call(b, split_idx)
        self.cut_pct = random.uniform(0, self.max_cut_pct)

    def encodes(self, ai:AudioTensor): return cutout(ai, self.cut_pct)

# Cell
@patch
def lose_signal(ai:AudioTensor, loss_pct):
    mask = (torch.rand_like(ai.data[0])>loss_pct).float()
    ai.data[...,:] *= mask
    return ai

# Cell
class SignalLoss(RandTransform):
    def __init__(self, p=0.5, max_loss_pct = 0.15):
        self.max_loss_pct = max_loss_pct
        super().__init__(p=p, as_item=True)

    def before_call(self, b, split_idx):
        super().before_call(b, split_idx)
        self.loss_pct = random.uniform(0, self.max_loss_pct)

    def encodes(self, ai:AudioTensor): return lose_signal(ai, self.loss_pct)

# Cell
# downmixMono was removed from torchaudio, we now just take the mean across channels
# this works for both batches and individual items
def DownmixMono():
    def _inner(ai: AudioTensor)->AudioTensor:
        """Randomly replaces amplitude of signal with 0. Simulates analog info loss"""
        downmixed = ai.data.contiguous().mean(-2).unsqueeze(-2)
        return AudioTensor(downmixed, ai.sr)
    return _inner

# Cell
def CropTime(duration, pad_mode=AudioPadType.Zeros):
    def _inner(sg:AudioSpectrogram)->AudioSpectrogram:
        '''Random crops full spectrogram to be length specified in ms by crop_duration'''
        sr, hop = sg.sr, sg.hop_length
        w_crop = int((sr*duration)/(1000*hop))+1
        w_sg   = sg.shape[-1]
        if     w_sg == w_crop: sg_crop = sg
        elif   w_sg <  w_crop: sg_crop = _tfm_pad_spectro(sg, w_crop, pad_mode=pad_mode)
        else:
            crop_start = random.randint(0, int(w_sg - w_crop))
            sg_crop = sg[:,:,crop_start:crop_start+w_crop]
            sg_crop.sample_start = int(crop_start*hop)
            sg_crop.sample_end   = sg_crop.sample_start + int(duration*sr)
        sg.data = sg_crop
        return sg
    return _inner

# Cell
def _tfm_pad_spectro(sg, width, pad_mode=AudioPadType.Zeros):
    '''Pad spectrogram to specified width, using specified pad mode'''
    c,y,x = sg.shape
    pad_m = pad_mode.lower()
    if pad_m in ["zeros", "zeros_after"]:
        padded = torch.zeros((c,y,width))
        start = random.randint(0, width-x) if pad_m == "zeros" else 0
        padded[:,:,start:start+x] = sg.data
        return padded
    elif pad_m == "repeat":
        repeats = width//x + 1
        return sg.repeat(1,1,repeats)[:,:,:width]
    else:
        raise ValueError(f"pad_mode {pad_m} not currently supported, only 'zeros', 'zeros_after', or 'repeat'")

# Cell
def MaskFreq(num_masks=1, size=20, start=None, val=None, **kwargs):
    def _inner(sg:AudioSpectrogram)->AudioSpectrogram:
        '''Google SpecAugment time masking from https://arxiv.org/abs/1904.08779.'''
        nonlocal start
        channel_mean = sg.contiguous().view(sg.size(0), -1).mean(-1)[:,None,None]
        mask_val = channel_mean if val is None else val
        c, y, x = sg.shape
        for _ in range(num_masks):
            mask = torch.ones(size, x) * mask_val
            if start is None: start= random.randint(0, y-size)
            if not 0 <= start <= y-size:
                raise ValueError(f"Start value '{start}' out of range for AudioSpectrogram of shape {sg.shape}")
            sg[:,start:start+size,:] = mask
            start = None
        return sg
    return _inner

# Cell
def MaskTime(num_masks=1, size=20, start=None, val=None, **kwargs):
    def _inner(sg:AudioSpectrogram)->AudioSpectrogram:
        sg.data = torch.einsum('...ij->...ji', sg)
        sg.data = MaskFreq(num_masks, size, start, val, **kwargs)(sg)
        sg.data = torch.einsum('...ij->...ji', sg)
        return sg
    return _inner

# Cell
def SGRoll(max_shift_pct=0.5, direction=0, **kwargs):
    '''Shifts spectrogram along x-axis wrapping around to other side'''
    if int(direction) not in [-1, 0, 1]:
        raise ValueError("Direction must be -1(left) 0(bidirectional) or 1(right)")
    def _inner(sg:AudioSpectrogram)->AudioSpectrogram:
        nonlocal direction
        direction = random.choice([-1, 1]) if direction == 0 else direction
        w = sg.shape[-1]
        roll_by = int(w*random.random()*max_shift_pct*direction)
        sg.data = sg.roll(roll_by, dims=-1)
        return sg
    return _inner

# Cell
def _torchdelta(sg:AudioSpectrogram, order=1, width=9):
    '''Converts to numpy, takes delta and converts back to torch, needs torchification'''
    if(sg.shape[1] < width):
        raise ValueError(f'''Delta not possible with current settings, inputs must be wider than
        {width} columns, try setting max_to_pad to a larger value to ensure a minimum width''')
    return AudioSpectrogram(torch.from_numpy(librosa.feature.delta(sg.numpy(), order=order, width=width)))

# Cell
def Delta(width=9):
    td = partial(_torchdelta, width=width)
    def _inner(sg:AudioSpectrogram)->AudioSpectrogram:
        new_channels = [torch.stack([c, td(c, order=1), td(c, order=2)]) for c in sg]
        sg.data = torch.cat(new_channels, dim=0)
        return sg
    return _inner

# Cell
def TfmResize(size, interp_mode="bilinear", **kwargs):
    '''Temporary fix to allow image resizing transform'''
    def _inner(sg:AudioSpectrogram)->AudioSpectrogram:
        nonlocal size
        if isinstance(size, int): size = (size, size)
        c,y,x = sg.shape
        sg.data = F.interpolate(sg.unsqueeze(0), size=size, mode=interp_mode, align_corners=False).squeeze(0)
        return sg
    return _inner

# Cell
def AudioBlock(cls=AudioTensor): return TransformBlock(type_tfms=cls.create, batch_tfms=IntToFloatTensor)