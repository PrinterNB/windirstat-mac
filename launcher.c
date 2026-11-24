/* Windirstat.app launcher, compiled to Contents/MacOS/Windirstat.

   Finder refuses to run a #!/bin/sh script placed in an app bundle, so the bundle
   needs a real executable here - the same reason a PyInstaller bundle has one.
   This finds the project folder (four levels up from the file itself), then runs
   windirstat.py through /bin/sh so the log redirection below works.

   Built by build.sh:  clang -o "$ENTRY" launcher.c
*/
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    static const char *fallback[] = {
        "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
        "/usr/local/bin/python3",
        "/opt/homebrew/bin/python3",
        "/usr/bin/python3",
        NULL
    };
    const char *py = NULL;
    const char *env_py = getenv("WINDIRSTAT_PY");
    int i;

    if (env_py && *env_py && access(env_py, 1) == 0)
        py = env_py;                       /* the interpreter build.sh saw */
    for (i = 0; !py && fallback[i]; i++)
        if (access(fallback[i], 1) == 0)
            py = fallback[i];
    if (!py) {
        fprintf(stderr, "windirstat: no python3 found\n");
        return 1;
    }

    /* Finder passes the full path of this executable, so walk up to the project */
    char up[8192];
    const char *self = (argc > 0 && strchr(argv[0], '/')) ? argv[0] : ".";
    snprintf(up, sizeof up, "%s", self);
    char *slash = strrchr(up, '/');
    if (slash)
        *slash = '\0';                                  /* .../Contents/MacOS */
    char up2[8192];
    snprintf(up2, sizeof up2, "%s/../../../..", up);    /* four up = project root */

    char proj[4096];
    if (!realpath(up2, proj)) {
        fprintf(stderr, "windirstat: cannot locate the project folder\n");
        return 1;
    }

    char cmd[16384];
    snprintf(cmd, sizeof cmd,
             "echo '[launcher] python=%s proj=%s' >>'%s/windirstat.log'; "
             "'%s' '%s/windirstat.py'",
             py, proj, proj, py, proj);
    for (i = 1; i < argc && strlen(cmd) + 8 < sizeof cmd; i++)
        snprintf(cmd + strlen(cmd), sizeof cmd - strlen(cmd), " '%s'", argv[i]);
    snprintf(cmd + strlen(cmd), sizeof cmd - strlen(cmd),
             " >>'%s/windirstat.log' 2>&1; "
             "echo \"[launcher] exited rc=$?\" >>'%s/windirstat.log'",
             proj, proj);

    char *args[] = { "/bin/sh", "-c", cmd, NULL };
    execv("/bin/sh", args);
    perror("windirstat: cannot start /bin/sh");
    return 1;
}
