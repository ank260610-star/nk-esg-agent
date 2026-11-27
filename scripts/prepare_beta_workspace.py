"""Offline V4 to a new owner-bound business copy; no existing task writes."""
import argparse
import json
from esg_app.beta_workspace import prepare_workspace

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('package')
    parser.add_argument('--output', required=True)
    parser.add_argument('--owner', required=True, help='Exact authenticated gateway username')
    args = parser.parse_args()
    print(json.dumps(prepare_workspace(args.package, args.output, args.owner), ensure_ascii=False, indent=2))
