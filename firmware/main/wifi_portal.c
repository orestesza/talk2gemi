#include "wifi_portal.h"
#include "ws_client.h"
#include "audio_stream.h"
#include "ble_link.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_http_server.h"
#include "esp_heap_caps.h"
#include "esp_mac.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "app.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include <string.h>
#include <stdlib.h>
#include <ctype.h>

static const char *TAG = "wifi_portal";

#define NVS_NAMESPACE "talk2gemi"
#define DEFAULT_PORT 8765

static char s_ssid[33] = {0};
static char s_pass[65] = {0};
static char s_server_ip[64] = {0};
static int s_server_port = DEFAULT_PORT;
static char s_sta_ip_str[16] = {0};

static esp_netif_t *s_sta_netif = NULL;
static esp_netif_t *s_ap_netif = NULL;
static httpd_handle_t s_httpd = NULL;
static TaskHandle_t s_dns_task = NULL;
static bool s_sta_started;
static bool s_setup_mode;
static wifi_status_cb_t s_cb = NULL;

static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1

static void wifi_event_handler(void* arg, esp_event_base_t event_base,
                               int32_t event_id, void* event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        s_sta_ip_str[0] = 0;
        xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
        ESP_LOGW(TAG, "Wi-Fi disconnected");
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_AP_STACONNECTED) {
        wifi_event_ap_staconnected_t *ev = (wifi_event_ap_staconnected_t *)event_data;
        ESP_LOGI(TAG, "AP station connected: " MACSTR " aid=%d (free=%u largest=%u)",
                 MAC2STR(ev->mac), ev->aid,
                 (unsigned)esp_get_free_heap_size(),
                 (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_AP_STADISCONNECTED) {
        wifi_event_ap_stadisconnected_t *ev = (wifi_event_ap_stadisconnected_t *)event_data;
        ESP_LOGI(TAG, "AP station disconnected: " MACSTR " aid=%d reason=%d (free=%u largest=%u)",
                 MAC2STR(ev->mac), ev->aid, ev->reason,
                 (unsigned)esp_get_free_heap_size(),
                 (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        esp_ip4addr_ntoa(&event->ip_info.ip, s_sta_ip_str, sizeof(s_sta_ip_str));
        ESP_LOGI(TAG, "Got IP: %s", s_sta_ip_str);
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        xEventGroupClearBits(s_wifi_event_group, WIFI_FAIL_BIT);
        if (s_cb) s_cb(WIFI_STATUS_CONNECTED, s_sta_ip_str);
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_AP_STAIPASSIGNED) {
        ip_event_ap_staipassigned_t *ev = (ip_event_ap_staipassigned_t *)event_data;
        ESP_LOGI(TAG, "AP DHCP assigned IP: " IPSTR " (free=%u largest=%u)",
                 IP2STR(&ev->ip),
                 (unsigned)esp_get_free_heap_size(),
                 (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
    }
}

static void load_nvs_config(void)
{
    nvs_handle_t h;
    if (nvs_open(NVS_NAMESPACE, NVS_READONLY, &h) == ESP_OK) {
        size_t len = sizeof(s_ssid);
        nvs_get_str(h, "ssid", s_ssid, &len);
        len = sizeof(s_pass);
        nvs_get_str(h, "password", s_pass, &len);
        len = sizeof(s_server_ip);
        nvs_get_str(h, "server_ip", s_server_ip, &len);
        int32_t port = DEFAULT_PORT;
        if (nvs_get_i32(h, "server_port", &port) == ESP_OK) {
            s_server_port = (int)port;
        }
        nvs_close(h);
        ESP_LOGI(TAG, "Loaded config: SSID='%s', Server='%s:%d'", s_ssid, s_server_ip, s_server_port);
    } else {
        ESP_LOGI(TAG, "No saved config found in NVS");
    }
}

static esp_err_t save_nvs_config(const char *ssid, const char *pass, const char *server_ip, int port)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h);
    if (err != ESP_OK) return err;

    nvs_set_str(h, "ssid", ssid);
    nvs_set_str(h, "password", pass);
    nvs_set_str(h, "server_ip", server_ip);
    nvs_set_i32(h, "server_port", port);
    err = nvs_commit(h);
    nvs_close(h);
    return err;
}

esp_err_t wifi_portal_init(wifi_status_cb_t cb)
{
    s_cb = cb;
    s_wifi_event_group = xEventGroupCreate();

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    load_nvs_config();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));

    return ESP_OK;
}

bool wifi_portal_has_config(void)
{
    return (strlen(s_ssid) > 0 && strlen(s_server_ip) > 0);
}

esp_err_t wifi_portal_connect_sta(int timeout_sec)
{
    if (s_setup_mode) return ESP_ERR_INVALID_STATE;
    if (strlen(s_ssid) == 0) return ESP_ERR_INVALID_STATE;
    wifi_ap_record_t current;
    if (esp_wifi_sta_get_ap_info(&current) == ESP_OK && s_sta_ip_str[0]) return ESP_OK;

    if (!s_sta_netif) {
        s_sta_netif = esp_netif_create_default_wifi_sta();
    }

    wifi_config_t wifi_config = {0};
    strncpy((char*)wifi_config.sta.ssid, s_ssid, sizeof(wifi_config.sta.ssid) - 1);
    strncpy((char*)wifi_config.sta.password, s_pass, sizeof(wifi_config.sta.password) - 1);

    xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT | WIFI_FAIL_BIT);
    if (!s_sta_started) {
        esp_err_t err = esp_wifi_set_mode(WIFI_MODE_STA);
        if (err == ESP_OK) err = esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
        if (err == ESP_OK) err = esp_wifi_start();
        if (err == ESP_OK) esp_wifi_set_ps(WIFI_PS_NONE);
        if (err != ESP_OK) return err;
        s_sta_started = true;
    }

    if (s_cb) s_cb(WIFI_STATUS_CONNECTING, s_ssid);
    ESP_LOGI(TAG, "Connecting to Wi-Fi: '%s'...", s_ssid);

    esp_wifi_connect();

    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group,
            WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
            pdFALSE, pdFALSE, pdMS_TO_TICKS(timeout_sec * 1000));

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "Connected to '%s'", s_ssid);
        return ESP_OK;
    }

    ESP_LOGW(TAG, "Failed to connect to '%s' within %d seconds", s_ssid, timeout_sec);
    if (s_cb) s_cb(WIFI_STATUS_FAILED, "Connect timeout");
    return ESP_FAIL;
}

static void url_decode(char *dst, const char *src, size_t dst_len) {
    char a, b;
    size_t i = 0;
    while (*src && i < dst_len - 1) {
        if ((*src == '%') &&
            ((a = src[1]) && (b = src[2])) &&
            (isxdigit(a) && isxdigit(b))) {
            if (a >= 'a') a -= 'a'-'A';
            if (a >= 'A') a -= ('A' - 10);
            else a -= '0';
            if (b >= 'a') b -= 'a'-'A';
            if (b >= 'A') b -= ('A' - 10);
            else b -= '0';
            *dst++ = 16*a + b;
            src += 3;
        } else if (*src == '+') {
            *dst++ = ' ';
            src++;
        } else {
            *dst++ = *src++;
        }
        i++;
    }
    *dst = '\0';
}

static void dns_server_task(void *arg)
{
    (void)arg;
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (sock < 0) {
        ESP_LOGE(TAG, "DNS socket failed");
        s_dns_task = NULL;
        vTaskDelete(NULL);
        return;
    }
    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port = htons(53),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    if (bind(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        ESP_LOGE(TAG, "DNS bind failed");
        close(sock);
        s_dns_task = NULL;
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "Captive DNS server listening on UDP :53");

    static uint8_t pkt[256];
    struct sockaddr_in client;
    socklen_t client_len = sizeof(client);

    while (1) {
        client_len = sizeof(client);
        int len = recvfrom(sock, pkt, sizeof(pkt) - 20, 0, (struct sockaddr *)&client, &client_len);
        if (len < 17) continue;
        // Only respond to standard queries (QR == 0, OPCODE == 0)
        if ((pkt[2] & 0xF8) != 0) continue;
        int qdcount = (pkt[4] << 8) | pkt[5];
        if (qdcount < 1) continue;

        // Walk QNAME of the first question
        int pos = 12;
        while (pos < len && pkt[pos] != 0) {
            if ((pkt[pos] & 0xC0) == 0xC0) {
                pos += 2;
                goto qname_done;
            }
            pos += pkt[pos] + 1;
        }
        if (pos >= len) continue;
        pos++; // skip terminating 0x00
qname_done:
        if (pos + 4 > len) continue;
        uint16_t qtype = ((uint16_t)pkt[pos] << 8) | pkt[pos + 1];
        pos += 4; // skip QTYPE and QCLASS; pos is now end of Question 1

        // Standard query response, recursion available, no error
        pkt[2] = 0x81;
        pkt[3] = 0x80;
        pkt[4] = 0x00;
        pkt[5] = 0x01; // QDCOUNT = 1
        pkt[8] = 0x00;
        pkt[9] = 0x00; // NSCOUNT = 0
        pkt[10] = 0x00;
        pkt[11] = 0x00; // ARCOUNT = 0 (strip EDNS0 OPT)

        if (qtype == 1 || qtype == 255) {
            // Type A (IPv4) or ANY -> answer with 192.168.4.1
            pkt[6] = 0x00;
            pkt[7] = 0x01; // ANCOUNT = 1
            uint8_t ans[16] = {
                0xc0, 0x0c,             // Pointer to QNAME at offset 12
                0x00, 0x01,             // TYPE A
                0x00, 0x01,             // CLASS IN
                0x00, 0x00, 0x00, 0x3c, // TTL 60s
                0x00, 0x04,             // RDLENGTH 4
                192, 168, 4, 1          // 192.168.4.1
            };
            memcpy(pkt + pos, ans, sizeof(ans));
            sendto(sock, pkt, pos + sizeof(ans), 0, (struct sockaddr *)&client, client_len);
        } else {
            // AAAA / HTTPS / other types -> NODATA (ANCOUNT = 0, NoError)
            pkt[6] = 0x00;
            pkt[7] = 0x00;
            sendto(sock, pkt, pos, 0, (struct sockaddr *)&client, client_len);
        }
    }
}

static esp_err_t http_root_handler(httpd_req_t *req)
{
    char *html = (char *)malloc(3072);
    if (!html) return ESP_ERR_NO_MEM;

    snprintf(html, 3072,
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no'>"
        "<title>Talk2Gemi Setup</title>"
        "<style>"
        "* { box-sizing: border-box; margin: 0; padding: 0; }"
        "body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #c9d1d9; padding: 16px; }"
        ".card { background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 20px; max-width: 400px; margin: 10px auto; box-shadow: 0 8px 24px rgba(0,0,0,0.6); }"
        "h2 { color: #58a6ff; text-align: center; font-size: 20px; margin-bottom: 4px; }"
        ".sub { color: #8b949e; text-align: center; font-size: 13px; margin-bottom: 16px; }"
        "label { display: block; margin-top: 14px; font-size: 13px; color: #8b949e; font-weight: 600; }"
        "input { width: 100%%; padding: 12px; margin-top: 6px; border-radius: 6px; border: 1px solid #30363d; background: #0d1117; color: #fff; font-size: 15px; }"
        "input:focus { border-color: #58a6ff; outline: none; }"
        "button { width: 100%%; margin-top: 24px; padding: 14px; background: #238636; color: #fff; border: none; border-radius: 6px; font-size: 16px; font-weight: bold; cursor: pointer; }"
        "button:hover { background: #2ea043; }"
        ".hint { font-size: 12px; color: #8b949e; margin-top: 16px; text-align: center; line-height: 1.5; }"
        "</style></head><body>"
        "<div class='card'>"
        "<h2>talk2gemi 配网</h2>"
        "<div class='sub'>Wi-Fi & Mac Relay Setup</div>"
        "<form method='POST' action='/save'>"
        "<label>Wi-Fi 名称 (SSID)</label>"
        "<input type='text' name='ssid' value='%s' placeholder='例如 MyHome_WiFi' required>"
        "<label>Wi-Fi 密码 (Password)</label>"
        "<input type='password' name='password' value='%s' placeholder='Wi-Fi 密码（无密码留空）'>"
        "<label>Mac 电脑中继 IP (Relay Server IP)</label>"
        "<input type='text' name='server_ip' value='%s' placeholder='Mac 客户端显示的局域网 IP' required>"
        "<label>中继端口 (Relay Port)</label>"
        "<input type='number' name='server_port' value='%d' required>"
        "<button type='submit'>保存并连接 / Save & Connect</button>"
        "</form>"
        "<div class='hint'>保存后卡片将自动重启并连接 Wi-Fi 与 Mac 客户端。<br>After saving, device will reboot and connect.</div>"
        "</div></body></html>",
        s_ssid, s_pass, s_server_ip[0] ? s_server_ip : "", s_server_port > 0 ? s_server_port : DEFAULT_PORT);

    httpd_resp_set_type(req, "text/html");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store, no-cache, must-revalidate");
    httpd_resp_set_hdr(req, "Connection", "close");
    esp_err_t res = httpd_resp_send(req, html, HTTPD_RESP_USE_STRLEN);
    free(html);
    return res;
}

static esp_err_t http_captive_redirect_handler(httpd_req_t *req)
{
    httpd_resp_set_status(req, "302 Found");
    httpd_resp_set_hdr(req, "Location", "http://192.168.4.1/");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store, no-cache, must-revalidate");
    httpd_resp_set_hdr(req, "Connection", "close");
    return httpd_resp_send(req, "Redirecting...", HTTPD_RESP_USE_STRLEN);
}

static esp_err_t http_404_redirect_handler(httpd_req_t *req, httpd_err_code_t err)
{
    (void)err;
    httpd_resp_set_status(req, "302 Found");
    httpd_resp_set_hdr(req, "Location", "http://192.168.4.1/");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store, no-cache, must-revalidate");
    httpd_resp_set_hdr(req, "Connection", "close");
    return httpd_resp_send(req, "Redirecting...", HTTPD_RESP_USE_STRLEN);
}

static esp_err_t http_save_handler(httpd_req_t *req)
{
    char buf[512] = {0};
    int ret = httpd_req_recv(req, buf, sizeof(buf) - 1);
    if (ret <= 0) return ESP_FAIL;

    char raw_ssid[64] = {0}, raw_pass[128] = {0}, raw_ip[64] = {0}, raw_port[16] = {0};
    char dec_ssid[33] = {0}, dec_pass[65] = {0}, dec_ip[64] = {0};
    int port = DEFAULT_PORT;

    char *token = strtok(buf, "&");
    while (token != NULL) {
        if (strncmp(token, "ssid=", 5) == 0) strncpy(raw_ssid, token + 5, sizeof(raw_ssid)-1);
        else if (strncmp(token, "password=", 9) == 0) strncpy(raw_pass, token + 9, sizeof(raw_pass)-1);
        else if (strncmp(token, "server_ip=", 10) == 0) strncpy(raw_ip, token + 10, sizeof(raw_ip)-1);
        else if (strncmp(token, "server_port=", 12) == 0) strncpy(raw_port, token + 12, sizeof(raw_port)-1);
        token = strtok(NULL, "&");
    }

    url_decode(dec_ssid, raw_ssid, sizeof(dec_ssid));
    url_decode(dec_pass, raw_pass, sizeof(dec_pass));
    url_decode(dec_ip, raw_ip, sizeof(dec_ip));
    if (strlen(raw_port) > 0) port = atoi(raw_port);

    ESP_LOGI(TAG, "Saving credentials: SSID='%s', Server='%s:%d'", dec_ssid, dec_ip, port);
    if (strcmp(dec_ip, s_server_ip)) {
        app_settings.host_id[0] = app_settings.mac_name[0] = app_settings.token[0] = 0;
        app_save();
    }
    save_nvs_config(dec_ssid, dec_pass, dec_ip, port);

    const char *resp = 
        "<!DOCTYPE html><html><body style='background:#0d1117;color:#3fb950;text-align:center;padding-top:60px;font-family:sans-serif;'>"
        "<h2>Config Saved Successfully!</h2>"
        "<p style='color:#8b949e;margin-top:15px;'>Device is restarting and connecting to Wi-Fi...</p>"
        "</body></html>";
    httpd_resp_set_hdr(req, "Connection", "close");
    httpd_resp_send(req, resp, HTTPD_RESP_USE_STRLEN);

    vTaskDelay(pdMS_TO_TICKS(1000));
    esp_restart();
    return ESP_OK;
}

esp_err_t wifi_portal_start_ap_web(void)
{
    if (s_setup_mode) return ESP_OK;
    s_setup_mode = true;
    if (s_cb) s_cb(WIFI_STATUS_AP_MODE, "Talk2Gemi-Setup");

    ESP_LOGI(TAG, "Preparing SoftAP: heap before teardown free=%u largest=%u",
             (unsigned)esp_get_free_heap_size(),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));

    /* Release optional subsystems (WebSocket, Audio tasks/queues, BLE/NimBLE)
     * so ESP32-C3 has abundant contiguous heap for SoftAP, DHCP, DNS, and HTTP.
     * Exiting Wi-Fi Setup always reboots the device cleanly. */
    ws_client_stop();
    audio_stream_stop();
    ble_link_stop();
    esp_wifi_stop();
    s_sta_started = false;
    vTaskDelay(pdMS_TO_TICKS(50));

    ESP_LOGI(TAG, "Heap after teardown: free=%u largest=%u",
             (unsigned)esp_get_free_heap_size(),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));

    if (!s_ap_netif) {
        s_ap_netif = esp_netif_create_default_wifi_ap();
    }

    wifi_config_t ap_config = {
        .ap = {
            .ssid = "Talk2Gemi-Setup",
            .ssid_len = strlen("Talk2Gemi-Setup"),
            .channel = 1,
            .password = "",
            .max_connection = 2,
            .authmode = WIFI_AUTH_OPEN
        },
    };

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &ap_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "SoftAP started: free=%u largest=%u",
             (unsigned)esp_get_free_heap_size(),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));

    if (!s_httpd) {
        esp_log_level_set("httpd_uri", ESP_LOG_WARN);
        esp_log_level_set("httpd_txrx", ESP_LOG_WARN);
        esp_log_level_set("httpd_parse", ESP_LOG_WARN);

        httpd_config_t config = HTTPD_DEFAULT_CONFIG();
        config.stack_size = 6144;
        config.max_open_sockets = 4;
        config.backlog_conn = 2;
        config.lru_purge_enable = true;
        config.recv_wait_timeout = 5;
        config.send_wait_timeout = 5;
        config.max_uri_handlers = 12;

        esp_err_t err = httpd_start(&s_httpd, &config);
        if (err == ESP_OK) {
            httpd_uri_t uri_get = {
                .uri      = "/",
                .method   = HTTP_GET,
                .handler  = http_root_handler,
                .user_ctx = NULL
            };
            httpd_register_uri_handler(s_httpd, &uri_get);

            httpd_uri_t uri_post = {
                .uri      = "/save",
                .method   = HTTP_POST,
                .handler  = http_save_handler,
                .user_ctx = NULL
            };
            httpd_register_uri_handler(s_httpd, &uri_post);

            // Android probes
            httpd_uri_t uri_gen204 = { .uri = "/generate_204", .method = HTTP_GET, .handler = http_captive_redirect_handler };
            httpd_register_uri_handler(s_httpd, &uri_gen204);

            httpd_uri_t uri_gen204_alt = { .uri = "/gen_204", .method = HTTP_GET, .handler = http_captive_redirect_handler };
            httpd_register_uri_handler(s_httpd, &uri_gen204_alt);

            // Apple iOS/macOS probe
            httpd_uri_t uri_apple = { .uri = "/hotspot-detect.html", .method = HTTP_GET, .handler = http_captive_redirect_handler };
            httpd_register_uri_handler(s_httpd, &uri_apple);

            // Windows probes
            httpd_uri_t uri_win1 = { .uri = "/connecttest.txt", .method = HTTP_GET, .handler = http_captive_redirect_handler };
            httpd_register_uri_handler(s_httpd, &uri_win1);

            httpd_uri_t uri_win2 = { .uri = "/ncsi.txt", .method = HTTP_GET, .handler = http_captive_redirect_handler };
            httpd_register_uri_handler(s_httpd, &uri_win2);

            // Catch-all 404 handler redirects everything else
            httpd_register_err_handler(s_httpd, HTTPD_404_NOT_FOUND, http_404_redirect_handler);

            ESP_LOGI(TAG, "HTTP Server started on 192.168.4.1:80 (free=%u largest=%u)",
                     (unsigned)esp_get_free_heap_size(),
                     (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
        } else {
            ESP_LOGE(TAG, "FAILED to start HTTP server: %s (free=%u largest=%u)",
                     esp_err_to_name(err),
                     (unsigned)esp_get_free_heap_size(),
                     (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
        }
    }

    if (!s_dns_task) {
        xTaskCreate(dns_server_task, "dns_srv", 4096, NULL, 3, &s_dns_task);
    }

    return ESP_OK;
}

const char* wifi_portal_get_ssid(void) { return s_ssid; }
const char* wifi_portal_get_server_ip(void) { return s_server_ip; }
int wifi_portal_get_server_port(void) { return s_server_port; }
const char* wifi_portal_get_sta_ip(void) { return s_sta_ip_str; }
bool wifi_portal_is_setup(void) { return s_setup_mode; }
esp_err_t wifi_portal_set_server(const char *host,int port) {
    struct in_addr address;
    if (!host || port < 1 || port > 65535 || inet_pton(AF_INET,host,&address) != 1) return ESP_ERR_INVALID_ARG;
    if (!strcmp(host,s_server_ip) && port==s_server_port) return ESP_OK;
    esp_err_t err=save_nvs_config(s_ssid,s_pass,host,port);
    if(err==ESP_OK){snprintf(s_server_ip,sizeof(s_server_ip),"%s",host);s_server_port=port;}
    return err;
}

// Clear only network credentials; retain server endpoint, Mac identity and BLE bonds.
esp_err_t wifi_portal_forget_sta(void) {
    if (s_setup_mode) return ESP_ERR_INVALID_STATE;
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h);
    if (err != ESP_OK) return err;
    err = nvs_erase_key(h, "ssid");
    if (err == ESP_ERR_NVS_NOT_FOUND) err = ESP_OK;
    if (err == ESP_OK) {
        err = nvs_erase_key(h, "password");
        if (err == ESP_ERR_NVS_NOT_FOUND) err = ESP_OK;
    }
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    if (err != ESP_OK) return err;
    memset(s_ssid, 0, sizeof(s_ssid));
    memset(s_pass, 0, sizeof(s_pass));
    s_sta_ip_str[0] = 0;
    esp_wifi_disconnect();
    esp_wifi_stop();
    s_sta_started = false;
    // Wi-Fi keeps a separate persistent driver configuration as well.
    wifi_config_t empty = {0};
    err = esp_wifi_set_mode(WIFI_MODE_STA);
    if (err == ESP_OK) err = esp_wifi_set_config(WIFI_IF_STA, &empty);
    xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT | WIFI_FAIL_BIT);
    if (s_cb) s_cb(WIFI_STATUS_IDLE, "Wi-Fi credentials forgotten");
    return err;
}

void wifi_portal_stop(void) {
    if (s_setup_mode) return;
    esp_wifi_disconnect();
    esp_wifi_stop();
    s_sta_started = false;
    s_sta_ip_str[0] = 0;
    xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT | WIFI_FAIL_BIT);
    ESP_LOGI(TAG, "Wi-Fi STA stopped; credentials preserved (free heap=%u)", (unsigned)esp_get_free_heap_size());
}
