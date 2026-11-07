"""Global, lexicographic workshop allocation using Google OR-Tools CP-SAT."""
from .engine import allocate, default_scoring, objective_order
from .models import AllocationResult, StageResult

__all__ = ["allocate", "default_scoring", "objective_order", "AllocationResult", "StageResult"]
