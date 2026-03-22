from openreward.environments import Server
from ade_bench import ADEBench

if __name__ == "__main__":
    Server([ADEBench]).run()
