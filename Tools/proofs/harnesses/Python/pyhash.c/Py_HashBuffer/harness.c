/* Proof harness for Py_HashBuffer() in Python/pyhash.c.
 *
 * Callers: bytes_hash() (Objects/bytesobject.c) passes ob_sval and
 * Py_SIZE(); unicode_hash() passes PyUnicode_DATA() and length * kind;
 * memory_hash() passes a buffer of view->len bytes; code objects, the
 * import cache and tracemalloc pass a struct, a pointer array or a C string
 * with its strlen().  So ptr points to len >= 0 readable bytes.  The buffer
 * here holds exactly len bytes, so any read past it is a bounds failure.
 *
 * This build uses Py_HASH_ALGORITHM = siphash13 and Py_HASH_CUTOFF = 0 (no
 * small-string DJBX33A path).  The hash secret is random at startup, so the
 * harness sets _Py_HashSecret to any value first.
 *
 * T1 (memory safety, no overflow beyond SipHash's intended modular
 * arithmetic), plus one cheap assertion: the empty buffer hashes to 0.
 * Stronger postconditions did not finish in 10 minutes: a T2 reference
 * (pysiphash() called a second time) even at 8 bytes, and "the result is
 * never -1" at 16 bytes; each makes the solver reason through SipHash.
 */
#include "Python/pyhash.c"

#define MAXLEN 8

void harness(void)
{
    _Py_HashSecret_t secret;
    _Py_HashSecret = secret;                    /* any secret */
    Py_ssize_t len;
    __CPROVER_assume(0 <= len && len <= MAXLEN);    /* proof bound */
    unsigned char *buf = malloc(len);
    /* The callers' buffers exist: the harness's own allocation may not fail. */
    __CPROVER_assume(buf != NULL);

    Py_hash_t r = Py_HashBuffer(buf, len);

    __CPROVER_assert(len != 0 || r == 0, "the empty buffer hashes to 0");
    free(buf);
}
