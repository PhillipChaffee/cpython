/* Proof harness for _Py_bytes_isalnum() in Objects/bytes_methods.c.
 *
 * Caller: stringlib_isalnum() (Objects/stringlib/ctype.h) passes
 * STRINGLIB_STR(self) and STRINGLIB_LEN(self): a buffer of len >= 0 bytes.
 * The buffer here holds exactly len bytes (no trailing NUL), so any read
 * past the end is a bounds failure.
 */
#include "Objects/bytes_methods.c"
/* Py_ISALNUM() reads _Py_ctype_table; link the real table, not a nondet one. */
#include "Python/pyctype.c"

#define MAXLEN 8

static int
ascii_alnum(unsigned char c)
{
    return ('0' <= c && c <= '9') || ('a' <= c && c <= 'z') || ('A' <= c && c <= 'Z');
}

void harness(void)
{
    Py_ssize_t len;
    __CPROVER_assume(0 <= len && len <= MAXLEN);    /* proof bound */
    char *buf = malloc(len);
    /* The caller's buffer exists: the harness's own allocation may not fail. */
    __CPROVER_assume(buf != NULL);

    const unsigned char *ubuf = (const unsigned char *)buf;

    int expect = len > 0;
    for (Py_ssize_t i = 0; i < len; i++) {
        if (!ascii_alnum(ubuf[i])) {
            expect = 0;
        }
    }

    PyObject *r = _Py_bytes_isalnum(buf, len);
    __CPROVER_assert(r == Py_True || r == Py_False, "returns a bool singleton");
    __CPROVER_assert((r == Py_True) == expect,
                     "True iff len > 0 and every byte is an ASCII letter or digit");
    free(buf);
}
