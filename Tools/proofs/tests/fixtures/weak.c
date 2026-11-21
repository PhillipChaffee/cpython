int clamp_sum(int a, int b)
{
    int s = a + b;
    if (s < 0)
        s = 0;
    return s;
}
