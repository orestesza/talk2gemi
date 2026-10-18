#include "ui.h"
#include "app.h"
#include "audio_stream.h"
#include "bsp_display.h"
#include "lvgl.h"
#include "esp_lvgl_port.h"
#include "esp_check.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "owl_assets.h"
#include "ui_icons.h"
#include "qrcodegen.h"
#include <stdio.h>
#include <string.h>
#define BG 0x080f1d
#define BLUE 0x36b8f4
#define GRAY 0x667487
static lv_obj_t *chrome,*battery,*title,*detail,*body,*footer,*level,*pet,*computer,*timing,*talk,*settings_hint;
static bool flags[4],in_menu,in_setup;
static int old_frame=-1,last_mode=-1;
static int64_t mode_since,monitor_at;
static void text(lv_obj_t *o,const char *s){if(strcmp(lv_label_get_text(o),s))lv_label_set_text(o,s);}
static void visible(lv_obj_t *o,bool yes){if(yes)lv_obj_remove_flag(o,LV_OBJ_FLAG_HIDDEN);else lv_obj_add_flag(o,LV_OBJ_FLAG_HIDDEN);}
static lv_obj_t *label(lv_obj_t *parent,int x,int y,int w,const lv_font_t *font,uint32_t color){
 lv_obj_t *o=lv_label_create(parent);lv_obj_set_pos(o,x,y);lv_obj_set_width(o,w);lv_label_set_long_mode(o,LV_LABEL_LONG_CLIP);
 lv_obj_set_style_text_font(o,font,0);lv_obj_set_style_text_color(o,lv_color_hex(color),0);lv_label_set_text(o,"");return o;
}
static void rect(lv_layer_t *layer,int x,int y,int w,int h,uint32_t color){
 lv_draw_rect_dsc_t d;lv_draw_rect_dsc_init(&d);d.bg_color=lv_color_hex(color);d.bg_opa=LV_OPA_COVER;
 lv_area_t a={x,y,x+w-1,y+h-1};lv_draw_rect(layer,&d,&a);
}
static void draw_wifi_qrcode(lv_layer_t *layer, int x0, int y0) {
    const char *qr_data = "WIFI:S:Talk2Gemi-Setup;T:nopass;;";
    uint8_t qrcode[qrcodegen_BUFFER_LEN_FOR_VERSION(3)];
    uint8_t tempBuffer[qrcodegen_BUFFER_LEN_FOR_VERSION(3)];
    bool ok = qrcodegen_encodeText(qr_data, tempBuffer, qrcode,
                                   qrcodegen_Ecc_LOW, 1, 3, qrcodegen_Mask_AUTO, true);
    if (!ok) return;
    int size = qrcodegen_getSize(qrcode);
    int scale = 4;
    int border = 2;
    int total = (size + border * 2) * scale;
    int ox = x0 + (240 - total) / 2;
    int oy = y0;

    rect(layer, ox, oy, total, total, 0xffffff);
    for (int y = 0; y < size; y++) {
        for (int x = 0; x < size; x++) {
            if (qrcodegen_getModule(qrcode, x, y)) {
                rect(layer, ox + (x + border) * scale,
                            oy + (y + border) * scale,
                            scale, scale, 0x000000);
            }
        }
    }
}
static void draw(lv_event_t *e){
 lv_layer_t *layer=lv_event_get_layer(e);lv_area_t a;lv_obj_get_coords(chrome,&a);
 const ui_icon_t kinds[]={ICON_WIFI,ICON_BT,ICON_COMPUTER,ICON_CLOUD};
 for(int i=0;i<4;i++)ui_draw_icon(layer,a.x1+12+i*35,a.y1+12,kinds[i],lv_color_hex(flags[i]?BLUE:GRAY));
 ui_draw_icon(layer,a.x1+160,a.y1+12,ICON_BATTERY,lv_color_hex(0xc2e2ee));
 rect(layer,a.x1+8,a.y1+37,224,1,0x263c58);
 if(in_menu)return;
 if(in_setup){
     draw_wifi_qrcode(layer, a.x1, a.y1 + 66);
     return;
 }
 rect(layer,a.x1+8,a.y1+263,224,1,0x263c58);rect(layer,a.x1+8,a.y1+291,224,1,0x263c58);
 rect(layer,a.x1+120,a.y1+264,1,55,0x263c58);
 ui_draw_icon(layer,a.x1+10,a.y1+270,ICON_COMPUTER,lv_color_hex(GRAY));
 ui_draw_icon(layer,a.x1+130,a.y1+270,ICON_CLOCK,lv_color_hex(GRAY));
 ui_draw_icon(layer,a.x1+10,a.y1+299,ICON_MIC,lv_color_hex(BLUE));
 ui_draw_icon(layer,a.x1+130,a.y1+299,ICON_GEAR,lv_color_hex(BLUE));
}
void ui_init(void){
 ESP_ERROR_CHECK(bsp_display_init());bsp_display_backlight(70);
 lvgl_port_cfg_t port=ESP_LVGL_PORT_INIT_CONFIG();port.task_stack=4096;ESP_ERROR_CHECK(lvgl_port_init(&port));
 lvgl_port_display_cfg_t display={.panel_handle=bsp_display_panel(),.io_handle=bsp_display_io(),.buffer_size=240*10,.double_buffer=false,.hres=240,.vres=320,
 .rotation={.swap_xy=false,.mirror_x=false,.mirror_y=false},.flags={.buff_dma=true,.swap_bytes=true}};
 assert(lvgl_port_add_disp(&display));assert(lvgl_port_lock(1000));
 lv_obj_t *s=lv_screen_active();lv_obj_set_style_bg_color(s,lv_color_hex(BG),0);lv_obj_remove_flag(s,LV_OBJ_FLAG_SCROLLABLE);
 chrome=lv_obj_create(s);lv_obj_remove_style_all(chrome);lv_obj_set_size(chrome,240,320);lv_obj_remove_flag(chrome,LV_OBJ_FLAG_SCROLLABLE);lv_obj_add_event_cb(chrome,draw,LV_EVENT_DRAW_MAIN,NULL);
 battery=label(s,180,12,56,&lv_font_montserrat_14,0xc2e2ee);
 pet=lv_image_create(s);lv_image_set_src(pet,&owl_frames[0]);lv_obj_set_pos(pet,40,44);
 title=label(s,12,213,216,&lv_font_montserrat_20,BLUE);lv_obj_set_style_text_align(title,LV_TEXT_ALIGN_CENTER,0);
 detail=label(s,12,240,216,&lv_font_montserrat_12,GRAY);lv_obj_set_style_text_align(detail,LV_TEXT_ALIGN_CENTER,0);
 body=label(s,16,108,208,&lv_font_montserrat_14,0xb7cbd4);lv_label_set_long_mode(body,LV_LABEL_LONG_WRAP);
 footer=label(s,12,280,216,&lv_font_montserrat_12,0x9baec5);
 computer=label(s,30,272,83,&lv_font_montserrat_12,0x9baec5);lv_label_set_long_mode(computer,LV_LABEL_LONG_SCROLL_CIRCULAR);
 timing=label(s,150,272,82,&lv_font_montserrat_12,0x9baec5);
 talk=label(s,30,301,83,&lv_font_montserrat_12,0xd7e8f2);text(talk,"Hold OK");
 settings_hint=label(s,150,301,82,&lv_font_montserrat_12,0xd7e8f2);text(settings_hint,"Hold " LV_SYMBOL_DOWN);
 level=lv_bar_create(s);lv_obj_set_pos(level,60,199);lv_obj_set_size(level,120,3);lv_bar_set_range(level,0,100);
 lv_obj_set_style_bg_color(level,lv_color_hex(0x15304b),0);lv_obj_set_style_bg_color(level,lv_color_hex(BLUE),LV_PART_INDICATOR);
 visible(body,false);visible(footer,false);visible(level,false);text(title,"Starting");
 lvgl_port_unlock();
}
void ui_render(const ui_snapshot_t *m){
 if(!lvgl_port_lock(100))return;
 bool setup = (m->state == UI_STATE_WIFI_SETUP);
 bool next[]={m->wifi,m->ble,m->relay,m->cloud};
 if(memcmp(flags,next,sizeof(flags))||in_menu!=m->menu||in_setup!=setup){
     memcpy(flags,next,sizeof(flags));in_menu=m->menu;in_setup=setup;lv_obj_invalidate(chrome);
 }
 char buf[64];if(m->battery>=0)snprintf(buf,sizeof(buf),"%d%%",m->battery);else snprintf(buf,sizeof(buf),"--%%");text(battery,buf);
 visible(pet,!m->menu&&!setup);
 visible(computer,!m->menu&&!setup);
 visible(timing,!m->menu&&!setup);
 visible(talk,!m->menu&&!setup);
 visible(settings_hint,!m->menu&&!setup);
 visible(body,m->menu||setup);
 visible(footer,m->menu||setup);
 visible(level,!m->menu&&!setup&&m->listening);
 if(setup){
     lv_obj_set_y(title,42);
     text(title,"Wi-Fi Setup");
     lv_obj_set_style_text_color(title,lv_color_hex(BLUE),0);
     lv_obj_set_y(detail,202);
     text(detail,"Scan QR to connect Wi-Fi");
     lv_obj_set_style_text_color(detail,lv_color_hex(0xffffff),0);
     lv_obj_set_y(body,220);
     lv_obj_set_height(body,75);
     lv_obj_set_style_text_align(body,LV_TEXT_ALIGN_CENTER,0);
     text(body,"Hotspot: Talk2Gemi-Setup\nPortal: 192.168.4.1\nEnter Wi-Fi & Relay Server");
     lv_obj_set_y(footer,297);
     text(footer,"Hold UP to exit (reboot)");
     lv_obj_set_style_text_color(footer,lv_color_hex(0x58a6ff),0);
     lv_obj_set_style_text_align(footer,LV_TEXT_ALIGN_CENTER,0);
 }else if(m->menu){
     lv_obj_set_style_text_align(body,LV_TEXT_ALIGN_LEFT,0);
     lv_obj_set_style_text_align(footer,LV_TEXT_ALIGN_LEFT,0);
     text(title,m->title);lv_obj_set_style_text_color(title,lv_color_hex(BLUE),0);text(detail,m->detail);text(body,m->body);text(footer,m->footer);
     lv_obj_set_y(title,52);lv_obj_set_y(detail,80);lv_obj_set_height(body,164);
 }else{
     lv_obj_set_style_text_align(body,LV_TEXT_ALIGN_LEFT,0);
     lv_obj_set_style_text_align(footer,LV_TEXT_ALIGN_LEFT,0);
     lv_obj_set_y(title,213);lv_obj_set_y(detail,240);text(computer,m->computer);
     if(m->latency_ms<0)snprintf(buf,sizeof(buf),"-- ms");else snprintf(buf,sizeof(buf),"%d ms",m->latency_ms);text(timing,buf);
     int mode;const char *name;
     if(m->listening){mode=1;name="Listening";}
     else if(m->playing){mode=3;name="Speaking";}
     else if(m->error){mode=5;name="Error";}
     else if(!strcmp(m->title,"PAIRING")){mode=6;name="Pairing";}
     else if(!m->relay||!m->cloud){mode=5;name=m->relay?"Connecting":"Offline";}
     else if(m->state==UI_STATE_THINKING){mode=2;name="Thinking";}
     else {mode=0;name="Ready";}
     int64_t now=esp_timer_get_time();if(mode!=last_mode){last_mode=mode;mode_since=now;}
     int64_t elapsed=(now-mode_since)/1000;int tick=elapsed/250,frame=0;
     if(mode==0)frame=(elapsed%5000>=4700)?1:0;
     else if(mode==1)frame=(tick%8<6)?2:0;
     else if(mode==2)frame=(tick%8<6)?3:0;
     else if(mode==3)frame=(tick%2)?4:0;
     else if(mode==5)frame=2;
     if(mode==0&&m->idle_ms>60000){frame=5;name="Resting";}
     if(frame!=old_frame){old_frame=frame;lv_image_set_src(pet,&owl_frames[frame]);}
     /* Position-only breathing: no decoder, extra buffers or animation timer. */
     lv_obj_set_y(pet,44+((mode==0&&tick%12>=6)?1:0));
     text(title,name);lv_obj_set_style_text_color(title,lv_color_hex(m->error?0xff8677:BLUE),0);
     if(m->volume_visible){snprintf(buf,sizeof(buf),"Volume: %d%%",m->volume);text(detail,buf);}
     else if(mode>=5)text(detail,m->detail);
     else text(detail,"");
     if(m->listening)lv_bar_set_value(level,m->level,LV_ANIM_OFF);
 }
 int64_t now_us=esp_timer_get_time();if(now_us-monitor_at>=5000000){monitor_at=now_us;lv_mem_monitor_t mon;lv_mem_monitor(&mon);
 ESP_LOGI("ui_mem","LVGL free=%u largest=%u used=%u%%",(unsigned)mon.free_size,(unsigned)mon.free_biggest_size,(unsigned)mon.used_pct);}
 lvgl_port_unlock();
}
void ui_set_state(ui_state_t state,const char *extra){app_set_state(state,extra);}
void ui_set_battery(int percent){(void)percent;}
void ui_set_transcript(const char *role,const char *s){(void)role;(void)s;}
