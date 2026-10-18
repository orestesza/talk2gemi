cmake_minimum_required(VERSION 3.16)
set(PROJECT_VER "2.0.0-dev")

# 复用 ai-passport 的通用 BSP 与已下载托管组件
set(EXTRA_COMPONENT_DIRS
    "${CMAKE_CURRENT_LIST_DIR}/../../ai-passport/components"
    "${CMAKE_CURRENT_LIST_DIR}/../../ai-passport/managed_components"
)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(talk2gemi)
