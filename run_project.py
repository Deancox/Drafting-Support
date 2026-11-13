import json
import sys
from drawing_qa.api import run_qa_project

if __name__ == "__main__":
    print(json.dumps(run_qa_project(sys.argv[1])))