import torch
from torch import nn
import torch.nn.functional as F
from torchaudio.transforms import MelSpectrogram
from torch.utils.checkpoint import checkpoint


class DiscriminatorLoss(nn.Module):
    def forward(self, real: list[list[torch.Tensor]], fake: list[list[torch.Tensor]]):
        loss = torch.tensor(
            0., device=real[0][0].device, dtype=real[0][0].dtype)

        for scale in real:
            loss += F.relu(1 - scale[-1]).mean()
        for scale in fake:
            loss += F.relu(1 + scale[-1]).mean()

        return loss


class GeneratorLoss(nn.Module):
    def forward(self, fake: list[list[torch.Tensor]]):
        loss = torch.tensor(
            0., device=fake[0][0].device, dtype=fake[0][0].dtype)
        for scale in fake:
            loss += -scale[-1].mean()
        return loss


class FeatureLoss(nn.Module):
    def __init__(self, n_blocks=3, n_features=16, n_layers=4):
        super().__init__()
        self.n_blocks = n_blocks
        self.n_features = n_features
        self.n_layers = n_layers

    def forward(self, real: list[list[torch.Tensor]], fake: list[list[torch.Tensor]]):
        loss = torch.tensor(
            0., device=real[0][0].device, dtype=real[0][0].dtype)
        feature_weights = 4.0 / (self.n_layers + 1)
        discriminator_weights = 1.0 / self.n_blocks
        wt = discriminator_weights * feature_weights
        for i in range(self.n_blocks):
            for j in range(len(fake[i]) - 1):
                loss += wt * F.l1_loss(fake[i][j], real[i][j].detach())
        return loss


class ReconstructionLoss(nn.Module):
    def __init__(self, sample_rate: int = 16_000, mel_bins=64, gradient_checkpointing: bool = False):
        super().__init__()
        self.sample_rate = sample_rate
        self.mel_bins = mel_bins
        self.gradient_checkpointing = gradient_checkpointing
        # TODO check which epsilon value we should use.
        self.epsilon = 1e-6

    def _calculate_for_scale(self):
        def custom_run(*inputs):
            original, generated, s_exp = inputs[0], inputs[1], inputs[2]
            s = 2 ** s_exp
            # Should satisfy n_fft >= win_length && ((n_fft // 2) + 1) >= n_mels.
            n_fft = 2 ** 11
            window_size = s
            hop_length = int(s / 4)
            mel_spectrogram = MelSpectrogram(
                sample_rate=self.sample_rate,
                win_length=window_size,
                n_fft=n_fft,
                hop_length=hop_length,
                n_mels=self.mel_bins
            ).to(original.device)
            orig_audio_spec = mel_spectrogram(original)
            generated_audio_spec = mel_spectrogram(generated)

            alpha_s = torch.sqrt(torch.tensor(s) / 2).to(original.device)
            l1_loss = torch.abs(orig_audio_spec - generated_audio_spec)
            l1_loss = l1_loss.sum(dim=1).mean()
            l2_log_loss = torch.pow(
                torch.log(orig_audio_spec + self.epsilon) - torch.log(generated_audio_spec + self.epsilon), exponent=2)
            l2_log_loss = l2_log_loss.sum(dim=1)
            l2_log_loss = torch.sqrt(l2_log_loss)
            l2_log_loss = l2_log_loss.mean()

            return l1_loss + alpha_s * l2_log_loss
        return custom_run

    def forward(self, original: torch.Tensor, generated: torch.Tensor):
        assert original.shape == generated.shape
        loss = torch.tensor(0., device=original.device, dtype=original.dtype)
        for s_exp in range(6, 12):
            if self.gradient_checkpointing:
                loss += checkpoint(
                    self._calculate_for_scale(),
                    original, generated, s_exp,
                    use_reentrant=False)
            else:
                loss += self._calculate_for_scale()(original, generated, s_exp)
        return loss
