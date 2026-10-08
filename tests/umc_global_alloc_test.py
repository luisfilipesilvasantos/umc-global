"""Teste UMC global: alocar 14GB numa GPU fisica de 12GB + matmul."""
import psutil
import torch

assert torch.cuda.is_available(), "CUDA indisponivel"

ram = psutil.virtual_memory()
free, total = torch.cuda.mem_get_info()
print("antes : virtual livre={:.2f} total={:.2f} GB | RAM usado={:.2f} GB".format(
    free / 1024 ** 3, total / 1024 ** 3, ram.used / 1024 ** 3), flush=True)

chunks = []
for i in range(7):
    # 2**30 elementos fp16 = 2GiB por chunk; full() toca as paginas todas
    chunks.append(torch.full((2 ** 30,), float(i), dtype=torch.float16))
print("alocado: 14.00 GB (VRAM fisica = 12 GB)", flush=True)

free, total = torch.cuda.mem_get_info()
ram = psutil.virtual_memory()
print("depois: virtual livre={:.2f} total={:.2f} GB | RAM usado={:.2f} GB".format(
    free / 1024 ** 3, total / 1024 ** 3, ram.used / 1024 ** 3), flush=True)

import umc_bridge_v3  # esta no src/ via sys.path do runner

fis = umc_bridge_v3.physical_used()
print("NVML  : fisico usado={:.2f} / total={:.2f} GB".format(
    fis / 1024 ** 3, umc_bridge_v3.physical_total() / 1024 ** 3), flush=True)

x = torch.randn(2048, 2048, device="cuda", dtype=torch.float16)
y = x @ x
s = float(y.float().abs().sum())
del x, y
print("matmul ok (soma={:.1f})".format(s), flush=True)

del chunks
torch.cuda.empty_cache()
free, total = torch.cuda.mem_get_info()
print("fim   : virtual livre={:.2f} GB | RAM usado={:.2f} GB | NVML fisico usado={:.2f} GB".format(
    free / 1024 ** 3, psutil.virtual_memory().used / 1024 ** 3,
    umc_bridge_v3.physical_used() / 1024 ** 3), flush=True)
print("UMC-ALLOC-TEST OK", flush=True)
