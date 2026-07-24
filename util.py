
import subprocess
import sys

def run_command(command_list, display_command=True, print_result=False):
    """Utility function to safely execute a command array"""
    if display_command:
        print(f"Executing: {' '.join(command_list)}")
    
    try:
        result = subprocess.run(command_list, check=True, text=True, capture_output=True)
        if result.stdout and print_result:
            print(result.stdout)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] Command failed with return code {e.returncode}!", file=sys.stderr)
        print(f"Details: {e.stderr}", file=sys.stderr)
        raise