import contextlib
import io
import traceback

def run_code(code):
    output = io.StringIO()
    scope = {"__name__": "__main__"}
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            exec(compile(code, "<user_script>", "exec"), scope, scope)
    except BaseException:
        output.write(traceback.format_exc())
    return output.getvalue() or "(No output)"
