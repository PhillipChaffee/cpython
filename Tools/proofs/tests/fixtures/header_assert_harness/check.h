/* Helper header in the harness directory: harness code too. */
static void check_dead(int s, int n)
{
    if (n > 10) {
        __CPROVER_assert(s == 12345, "dead code in check.h");
    }
}
