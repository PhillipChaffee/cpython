/* inventory fixture: line numbers matter */
int add(int a, int b)
{ return a + b; }

int clamp(int x, int lo, int hi)
{
    if (x < lo) return lo;
    if (x > hi) return hi;
    return x;
}

#if 0
int dead_fn(int a) { return a; }
#endif
