"""Validate a V4 package and write a fresh isolated staging directory."""
import argparse
import json
from esg_app.import_v4 import dry_run, ImportValidationError

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('package')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        result = dry_run(args.package, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ImportValidationError, FileExistsError) as exc:
        parser.exit(2, f'Import rejected: {exc}\n')
