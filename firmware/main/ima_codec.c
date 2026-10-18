#include "ima_codec.h"
#include <string.h>
static const int step_table[89] = {
7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,
73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,
449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,1707,1878,
2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,5358,5894,6484,7132,
7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,20350,22385,
24623,27086,29794,32767};
static const int adjust[8] = {-1,-1,-1,-1,2,4,6,8};
static int clamp(int n,int low,int high) {return n<low?low:n>high?high:n;}
static int expand(int code, int *predictor, int *index) {
    int step=step_table[*index], diff=step>>3;
    if(code&1) diff+=step>>2;
    if(code&2) diff+=step>>1;
    if(code&4) diff+=step;
    *predictor=clamp(*predictor+((code&8)?-diff:diff),-32768,32767);
    *index=clamp(*index+adjust[code&7],0,88);
    return *predictor;
}
size_t ima_encode(const int16_t *pcm,size_t n,uint8_t *out,size_t capacity) {
    size_t size=6+n/2;
    if(!pcm||!out||!n||n>1024||capacity<size) return 0;
    memset(out,0,size);
    int predictor=pcm[0], index=0;
    out[0]=predictor&255;out[1]=(predictor>>8)&255;
    out[4]=n&255;out[5]=(n>>8)&255;
    for(size_t i=1;i<n;i++) {
        int diff=pcm[i]-predictor, code=0, step=step_table[index];
        if(diff<0) {code=8;diff=-diff;}
        if(diff>=step){code|=4;diff-=step;}
        if(diff>=step/2){code|=2;diff-=step/2;}
        if(diff>=step/4) code|=1;
        expand(code,&predictor,&index);
        out[6+(i-1)/2]|=code<<(((i-1)%2)*4);
    }
    return size;
}
size_t ima_decode(const uint8_t *data,size_t length,int16_t *out,size_t capacity) {
    if(!data||length<6||!out) return 0;
    size_t n=data[4]|data[5]<<8;
    int predictor=(int16_t)(data[0]|data[1]<<8),index=data[2];
    if(!n||n>1024||capacity<n||index>88||data[3]||length!=6+n/2) return 0;
    out[0]=predictor;
    for(size_t i=1;i<n;i++)out[i]=expand((data[6+(i-1)/2]>>(((i-1)%2)*4))&15,&predictor,&index);
    return n;
}
