/* Proof harness for _Py_bytes_lower() in Objects/bytes_methods.c.
 *
 * Callers: stringlib_lower() (Objects/stringlib/ctype.h) writes into a
 * fresh bytes/bytearray of the same length; _Py_bytes_capitalize() lowers
 * s + 1 into result + 1 with len - 1 >= 0 (it returns early for len == 0);
 * ascii_upper_or_lower() (Objects/unicodeobject.c) lowers an ASCII str's
 * data into a fresh str's data.  So result and cptr are separate buffers of
 * len >= 0 bytes.  The loop reads cptr[i] before it writes result[i], so
 * the harness lets them alias too (a superset of the callers' inputs).
 * Buffers hold exactly len bytes, so any access past them is a bounds
 * failure.
 *
 * Postcondition (T2): result[i] is cptr0[i] with 'A'..'Z' mapped to
 * 'a'..'z' and every other byte unchanged.
 */
#include "Objects/bytes_methods.c"
/* Py_TOLOWER() reads _Py_ctype_tolower; link the real table, not a nondet one. */
#include "Python/pyctype.c"

#define MAXLEN 8

void harness(void)
{
    Py_ssize_t len;
    __CPROVER_assume(0 <= len && len <= MAXLEN);    /* proof bound */
    char *src = malloc(len);
    char *src0 = malloc(len);
    /* The callers' buffers exist: the harness's own allocations may not fail. */
    __CPROVER_assume(src != NULL && src0 != NULL);
    int alias;
    char *dst = alias ? src : malloc(len);
    __CPROVER_assume(dst != NULL);
    for (Py_ssize_t i = 0; i < len; i++) {
        src0[i] = src[i];
    }

    _Py_bytes_lower(dst, src, len);

    for (Py_ssize_t i = 0; i < len; i++) {
        unsigned char c = (unsigned char)src0[i];
        unsigned char want = ('A' <= c && c <= 'Z') ? (unsigned char)(c + ('a' - 'A')) : c;
        __CPROVER_assert((unsigned char)dst[i] == want, "result[i] is cptr[i] lowered (ASCII only)");
    }
    if (!alias) {
        for (Py_ssize_t i = 0; i < len; i++) {
            __CPROVER_assert(src[i] == src0[i], "cptr is unchanged");
        }
        free(dst);
    }
    free(src);
    free(src0);
}
