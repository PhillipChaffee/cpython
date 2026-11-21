/* Proof harness for PyOS_strtoul() in Python/mystrtoul.c.
 *
 * Callers: PyOS_strtol() (Python/mystrtoul.c) forwards its arguments;
 * Parser/pegen.c parsenumber_raw() passes a NUL-terminated token with
 * ptr = &end and base 0.  ptr may be NULL per the function's own comment.
 * The string buffer holds exactly len + 1 bytes, so a read past the NUL is a
 * bounds failure.
 */
#include "Python/mystrtoul.c"
/* Py_ISSPACE() reads _Py_ctype_table and the digit loop reads
 * _PyLong_DigitValue; link the real tables, not nondet ones. */
#include "Python/pyctype.c"
#include "Objects/longobject.c"

#define MAXLEN 12

void harness(void)
{
    Py_ssize_t len;
    __CPROVER_assume(0 <= len && len <= MAXLEN);    /* proof bound */
    char *buf = malloc(len + 1);
    buf[len] = '\0';

    int base;
    /* Documented contract above PyOS_strtoul(): "Base must be 0 or between
     * 2 and 36 (inclusive)". */
    __CPROVER_assume(base == 0 || (base >= 2 && base <= 36));

    char *end;
    int want_end;
    char **ptr = want_end ? &end : NULL;

    (void)PyOS_strtoul(buf, ptr, base);
    if (ptr != NULL) {
        __CPROVER_assert(buf <= end && end <= buf + len, "*ptr points inside the buffer");
    }
    free(buf);
}
