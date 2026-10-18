#pragma once
#include <stdbool.h>
#include <stdint.h>
typedef struct {int64_t last_activity, sleep_anchor;bool usb,desktop,screen_off,wake_guard;} idle_policy_t;
/* All times in monotonic microseconds. Radio/status traffic is not activity. */
static inline void idle_policy_usb(idle_policy_t *p,bool connected,int64_t now){
    if(p->usb&&!connected)p->sleep_anchor=now;
    p->usb=connected;
}
static inline int idle_policy_step(idle_policy_t *p,int64_t now,bool busy){
    if(busy){p->last_activity=now;return 0;}
    int result=0;
    if(!p->screen_off&&now-p->last_activity>=300000000LL){p->screen_off=true;result|=1;}
    int64_t anchor=p->last_activity>p->sleep_anchor?p->last_activity:p->sleep_anchor;
    if(!p->usb&&!p->desktop&&now-anchor>=3600000000LL)result|=2;
    return result;
}
/* Wake press and all events through release are consumed. */
static inline bool idle_policy_key(idle_policy_t *p,bool press,bool release){
    if(p->screen_off){if(press){p->screen_off=false;p->wake_guard=true;}return false;}
    if(p->wake_guard){if(release)p->wake_guard=false;return false;}
    return true;
}
