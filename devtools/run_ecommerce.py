"""Run the existing restricted Flask app without reloader; Qwen is explicit."""
import argparse
from devtools.run_local import configure_offline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qwen', action='store_true', help='Enable the existing server-side Qwen credential')
    args = parser.parse_args()
    if args.qwen:
        from devtools.qwen_config import configure_qwen, read_user_key
        configure_qwen(read_user_key())
    else:
        configure_offline()
    from data_formulator.app import app
    app.run(host='127.0.0.1', port=5567, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
