#include "idle_policy.h"
#include <assert.h>
int main(void){
    idle_policy_t p={0};
    assert(idle_policy_step(&p,299999999,0)==0);
    assert(idle_policy_step(&p,300000000,0)==1);
    assert(!idle_policy_key(&p,1,0));assert(p.wake_guard&&!p.screen_off);
    assert(!idle_policy_key(&p,0,0));assert(!idle_policy_key(&p,0,1));assert(idle_policy_key(&p,1,0));
    p.last_activity=0;p.screen_off=true;
    assert(idle_policy_step(&p,3599999999LL,0)==0);assert(idle_policy_step(&p,3600000000LL,0)==2);
    p.usb=true;assert(idle_policy_step(&p,7200000000LL,0)==0);
    idle_policy_usb(&p,false,7200000000LL);assert(idle_policy_step(&p,10799999999LL,0)==0);
    assert(idle_policy_step(&p,10800000000LL,0)==2);
    p.desktop=true;assert(idle_policy_step(&p,18000000000LL,0)==0);
    p.desktop=false;assert(idle_policy_step(&p,18000000000LL,true)==0);
    assert(idle_policy_step(&p,18000000001LL,false)==0);
    p.wake_guard=true;assert(!idle_policy_key(&p,1,0));assert(!idle_policy_key(&p,0,1));assert(idle_policy_key(&p,1,0));
    return 0;
}
