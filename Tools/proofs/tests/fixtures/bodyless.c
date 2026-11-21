int ext_lookup(int v);
int never_called(int v);

int lookup(int v)
{
    if (v > 1000)
        return never_called(v);
    return ext_lookup(v);
}
