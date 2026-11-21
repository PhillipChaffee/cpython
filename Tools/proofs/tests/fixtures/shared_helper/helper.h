/* Proof code outside any harness directory: no audit rule would check it. */
static void shared_check(int s)
{
    __CPROVER_assert(s == s, "shared helper assertion");
}
