# SPDX-License-Identifier: Apache-2.0
# Synthetic CEF host for SimMirror's CEF adapter. Configure with an extracted CEF binary distribution:
#   cmake -S examples/cef-app-sdk -B <build> -G Ninja -DCEF_ROOT=<cef_binary_...> -DCMAKE_BUILD_TYPE=Debug
cmake_minimum_required(VERSION 3.21)
project(simmirror_cef_sample LANGUAGES C CXX OBJCXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_OBJCXX_STANDARD 20)
set(CMAKE_OBJCXX_STANDARD_REQUIRED ON)

if(NOT DEFINED CEF_ROOT)
  message(FATAL_ERROR "CEF_ROOT must name an extracted CEF binary distribution")
endif()
set(CMAKE_MODULE_PATH ${CMAKE_MODULE_PATH} "${CEF_ROOT}/cmake")
find_package(CEF REQUIRED)
include(cef_macros)
include(cef_variables)
add_subdirectory(${CEF_LIBCEF_DLL_WRAPPER_PATH} libcef_dll_wrapper)

option(SIM_MIRROR_CEF_BRIDGE "Compile SimMirror's Debug CEF bridge into the sample" ON)

set(CEF_TARGET "simmirror_cef_sample")
SET_CEF_TARGET_OUT_DIR()
set(CEF_APP "${CEF_TARGET_OUT_DIR}/CEF Sample.app")
set(SAMPLE_HELPER_TARGET "simmirror_cef_sample_Helper")
set(SAMPLE_HELPER_NAME "CEF Sample Helper")
set(SDK_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../sdk/cef")

set(SAMPLE_DEFINES)
if(SIM_MIRROR_CEF_BRIDGE)
  list(APPEND SAMPLE_DEFINES SIM_MIRROR_CEF_BRIDGE=1)
endif()

add_executable(${CEF_TARGET} MACOSX_BUNDLE src/main_mac.mm src/sample_app.cc src/sample_app.h src/page.html)
SET_EXECUTABLE_TARGET_PROPERTIES(${CEF_TARGET})
target_compile_definitions(${CEF_TARGET} PRIVATE ${SAMPLE_DEFINES})
target_include_directories(${CEF_TARGET} PRIVATE src "${SDK_DIR}/include")
target_link_libraries(${CEF_TARGET} libcef_dll_wrapper ${CEF_STANDARD_LIBS})
set_source_files_properties(src/page.html PROPERTIES MACOSX_PACKAGE_LOCATION Resources)
set_target_properties(${CEF_TARGET} PROPERTIES
  OUTPUT_NAME "CEF Sample"
  MACOSX_BUNDLE_INFO_PLIST "${CMAKE_CURRENT_SOURCE_DIR}/macos/Info.plist.in"
  MACOSX_BUNDLE_GUI_IDENTIFIER "dev.simmirror.CEFSample")
COPY_MAC_FRAMEWORK("${CEF_TARGET}" "${CEF_BINARY_DIR}" "${CEF_APP}")

foreach(_suffix_list ${CEF_HELPER_APP_SUFFIXES})
  string(REPLACE ":" ";" _suffix_list ${_suffix_list})
  list(GET _suffix_list 0 _name_suffix)
  list(GET _suffix_list 1 _target_suffix)
  list(GET _suffix_list 2 _plist_suffix)
  set(_helper_target "${SAMPLE_HELPER_TARGET}${_target_suffix}")
  set(_helper_name "${SAMPLE_HELPER_NAME}${_name_suffix}")
  set(_helper_plist "${CMAKE_CURRENT_BINARY_DIR}/helper-Info${_target_suffix}.plist")
  file(READ "${CMAKE_CURRENT_SOURCE_DIR}/macos/helper-Info.plist.in" _plist)
  string(REPLACE "\${EXECUTABLE_NAME}" "${_helper_name}" _plist "${_plist}")
  string(REPLACE "\${BUNDLE_ID_SUFFIX}" "${_plist_suffix}" _plist "${_plist}")
  file(WRITE "${_helper_plist}" "${_plist}")
  add_executable(${_helper_target} MACOSX_BUNDLE src/helper.cc src/sample_app.cc src/sample_app.h)
  SET_EXECUTABLE_TARGET_PROPERTIES(${_helper_target})
  target_compile_definitions(${_helper_target} PRIVATE ${SAMPLE_DEFINES} SAMPLE_HELPER=1)
  target_include_directories(${_helper_target} PRIVATE src "${SDK_DIR}/include")
  target_link_libraries(${_helper_target} libcef_dll_wrapper ${CEF_STANDARD_LIBS})
  set_target_properties(${_helper_target} PROPERTIES
    MACOSX_BUNDLE_INFO_PLIST "${_helper_plist}" OUTPUT_NAME "${_helper_name}")
  add_dependencies(${CEF_TARGET} "${_helper_target}")
  add_custom_command(TARGET ${CEF_TARGET} POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory "${CEF_TARGET_OUT_DIR}/${_helper_name}.app"
            "${CEF_APP}/Contents/Frameworks/${_helper_name}.app" VERBATIM)
endforeach()
