import os

# Clinic scenario tests stay deterministic. The live voice worker calls Qwen.
os.environ["CLINIC_USE_LLM"] = "0"
