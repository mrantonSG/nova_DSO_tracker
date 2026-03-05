/**
 * Simplified Chinese translations for Nova DSO Tracker
 */
window.NOVA_I18N = window.NOVA_I18N || {};
window.NOVA_I18N.zh = {
    // ========================================================================
    // COMMON / GENERAL
    // ========================================================================
    "loading": "加载中...",
    "calculating": "计算中...",
    "error": "错误",
    "success": "成功",
    "cancel": "取消",
    "save": "保存",
    "delete": "删除",
    "edit": "编辑",
    "close": "关闭",
    "confirm": "确认",
    "yes": "是",
    "no": "否",
    "name": "名称",
    "description": "描述",
    "notes": "备注",
    "date": "日期",
    "time": "时间",
    "na": "N/A",

    // ========================================================================
    // DASHBOARD
    // ========================================================================
    "dashboard": "仪表板",
    "objects": "天体",
    "journal": "日志",
    "heatmap": "热力图",
    "outlook": "展望",
    "inspiration": "灵感",

    // Saved Views
    "saved_views": "保存的视图",
    "saved_views_placeholder": "-- 保存的视图 --",
    "error_loading_views": "加载视图错误",
    "name_required": "名称是必填项",
    "error_saving_view": "保存视图错误: {error}",
    "error_deleting_view": "删除视图错误: {error}",
    "confirm_delete_view": "您确定要删除视图\"{name}\"吗？",
    "error_load_view_data": "错误：无法从缓存加载视图数据。",

    // Simulation Mode
    "simulation": "模拟",
    "simulation_mode": "模拟模式",
    "simulated": "已模拟",
    "mode": "模式",
    "update": "更新",

    // Data Loading
    "data_load_failed": "数据加载失败: {error}",

    // ========================================================================
    // OBJECT TABLE
    // ========================================================================
    "object": "天体",
    "common_name": "常用名",
    "constellation": "星座",
    "type": "类型",
    "magnitude": "星等",
    "altitude": "高度",
    "azimuth": "方位角",
    "transit_time": "中天时间",
    "observable_duration": "可观测时长",
    "max_altitude": "最大高度",
    "moon_separation": "月距角",
    "trend": "趋势",
    "sb": "面亮度",
    "size": "大小",
    "best_month": "最佳月份",
    "current": "当前",
    "local_time": "本地时间",
    "minutes": "分钟",

    // Status Strip
    "location": "地点",
    "moon": "月相",
    "dusk": "黄昏",
    "dawn": "黎明",

    // ========================================================================
    // GRAPH VIEW / OBJECT DETAIL
    // ========================================================================
    "sep": "分离",
    "failed_update_active_project": "更新活动项目失败: {error}",
    "successfully_updated_active_project": "已成功将{object}的活动项目状态更新为{status}",
    "simbad_requires_internet": "SIMBAD需要活动的互联网连接来加载数据。",
    "simbad_requires_internet_short": "SIMBAD需要活动的互联网连接。",
    "no_imaging_opportunities": "在您的搜索条件内未找到良好的成像机会。",
    "error_loading_opportunities": "加载机会错误: {error}",
    "failed_load_opportunities": "加载成像机会失败。详情请查看控制台。({error})",
    "add_to_calendar": "添加到日历",
    "view_inspiration": "查看灵感",
    "add_own_inspiration": "添加您自己的灵感！",
    "seeing_automated_survey_images": "您当前看到的是自动调查图像（DSS2）。",
    "display_own_astrophotos": "您知道吗？您可以在这里展示自己的天文摄影作品或最爱的参考图片。",
    "go_to_config_manage_objects": "前往<strong>配置 &gt; 管理对象</strong>，为您的目标添加\"灵感内容\"（图片URL、署名、描述）。",

    // ========================================================================
    // OBJECTS SECTION
    // ========================================================================
    "showing_objects": "显示 {count} 个天体",
    "showing_objects_of": "显示 {total} 个中的 {visible} 个天体",
    "no_objects_selected": "未选择天体。",
    "confirm_bulk_action": "您确定要{action} {count} 个天体吗？",
    "bulk_action_failed": "批量操作失败。请查看控制台。",
    "bulk_fetch_details_failed": "批量获取详情失败。请查看控制台。",
    "fetching_details_for": "正在获取 {count} 个天体的详情...",
    "no_potential_duplicates": "基于坐标未找到潜在的重复项。",
    "all_duplicates_resolved": "所有重复项已解决！",
    "error_scanning_duplicates": "扫描重复项错误。",
    "merge_confirm": "将'{merge}'合并到'{keep}'中？\n\n这将：\n1. 将{merge}的日志/项目重新链接到{keep}\n2. 复制{merge}的备注\n3. 永久删除{merge}",
    "keep_a_merge_b": "保留A，合并B",
    "keep_b_merge_a": "保留B，合并A",
    "no_telescopes": "未定义望远镜。",
    "no_cameras": "未定义相机。",
    "no_reducers": "未定义减焦镜。",
    "no_rigs": "未定义配置。",
    "selected": "已选择",
    "please_enter_object_id": "请输入天体标识符。",
    "checking_local_library": "正在检查您的本地库中的{name}...",
    "object_found_library": "在您的库中找到天体'{name}'。正在加载以编辑。",
    "object_not_found_simbad": "在本地库中未找到天体。正在检查SIMBAD...",
    "found_details_loaded": "找到: {name}。已从SIMBAD加载详情。",
    "error_fetching_simbad": "错误: {error}。\n您现在可以手动添加天体并点击'确认'。",
    "warning_ra_degrees": "警告: RA值({ra}) > 24，表示为度数。\n\n您要自动转换为{corrected}小时吗？",
    "importing_catalog": "正在导入'{name}'...\n\n这将使用服务器数据更新您的库：\n• 将添加此包中的新天体。\n• 现有天体将更新为最新图像/描述。\n• 您的个人项目备注、状态和构图保持安全。\n\n您要继续吗？",

    // ========================================================================
    // CONFIG FORM
    // ========================================================================
    "update_component": "更新组件",
    "update_rig": "更新配置",
    "confirm_delete_component": "删除组件是永久性的，无法撤销。您确定吗？",
    "confirm_delete_rig": "您确定要删除配置'{name}'吗？",
    "select_telescope": "-- 选择望远镜 --",
    "select_camera": "-- 选择相机 --",
    "none": "-- 无 --",
    "telescope": "望远镜",
    "camera": "相机",
    "reducer_extender": "减焦镜/增倍镜",
    "guiding": "导星",
    "owner": "所有者",
    "import": "导入",
    "imported": "已导入",
    "importing": "导入中...",
    "confirm_import_item": "您确定要导入此{type}吗？",
    "import_failed": "导入失败。详情请查看控制台。",
    "no_shared_objects": "未找到其他用户共享的天体。",
    "no_shared_components": "未找到其他用户共享的组件。",
    "no_shared_views": "未找到其他用户共享的视图。",
    "error_loading_shared": "加载共享项目错误。",
    "view": "查看",
    "saving": "保存中...",
    "saved": "已保存！",
    "error_saving": "保存错误: {error}",
    "network_error_saving": "保存天体时发生网络错误。",
    "connecting": "连接中...",
    "importing_please_wait": "正在导入{entity}，请稍候...",
    "import_failed_server": "导入失败：服务器返回状态{status}",
    "upload_error": "上传错误: {error}",
    "upload_failed": "上传失败。请查看控制台。",
    "done": "完成",
    "shared_notes_for": "{name}的共享备注",
    "confirm_fetch_details": "这将扫描您的所有天体并从外部数据库获取缺失的详情（类型、星等、大小等）。\n\n根据您的库大小，这可能需要一些时间。\n\n继续吗？",
    "connection_lost_refreshing": "连接丢失。正在刷新页面...",
    "error_preparing_print": "准备打印视图错误: {error}",

    // Sampling
    "oversampled": "过采样",
    "slightly_oversampled": "轻微过采样",
    "good_sampling": "良好采样",
    "effective_fl": "有效焦距",
    "image_scale": "图像比例",
    "field_of_view": "视场",
    "guiding": "导星",
    "slightly_undersampled": "轻微欠采样",
    "undersampled": "欠采样",
    "px_fwhm": "像素/FWHM",
    "tip_binning": "提示: 2x2合并将产生约{scale}\"/px ({sampling} 像素/FWHM)",
    "check_software_max": " — 检查您的软件最大值",

    // ========================================================================
    // JOURNAL SECTION
    // ========================================================================
    "report_frame_not_found": "未找到报告框架。",
    "preparing_print_view": "准备打印视图...",
    "merging_session": "合并会话 {current}/{total}...",
    "check_popup": "检查弹窗...",
    "appendix_session": "附录: 会话 {number}",
    "add_new_session": "添加新会话",
    "add_session": "添加会话",
    "save_changes": "保存更改",
    "editing_session": "编辑会话: {date}",
    "visibility": "可见性: {date}",
    "altitude_deg": "高度 (°)",
    "recommendation": "建议: {pixels} 像素",
    "time_limit_reached": "(已达时间限制)",
    "max_real_subs": "(最大 {max} | 实际 {real})",

    // ========================================================================
    // BASE JS / GLOBAL
    // ========================================================================
    "loading_help_content": "加载帮助内容...",
    "help_content_empty": "错误：返回的帮助内容为空。",
    "network_error_help": "网络错误：无法加载帮助主题'{topic}'。",
    "latest_version": "最新版本: v{version}",

    // ========================================================================
    // MESSAGES / ALERTS
    // ========================================================================
    "saved_successfully": "保存成功",
    "deleted_successfully": "删除成功",
    "confirm_delete": "确定要删除吗？",
    "unsaved_changes": "有未保存的更改。确定要离开吗？",
    "network_error": "网络错误。请重试。",
    "session_expired": "会话已过期。请重新登录。",
    "error_with_message": "错误: {message}",
    "failed_with_error": "失败: {error}",

    // ========================================================================
    // CHART / GRAPH
    // ========================================================================
    "altitude_chart": "高度图",
    "altitude_degrees": "高度 (°)",
    "time_hours": "时间（小时）",
    "tonight": "今晚",
    "show_framing": "显示构图",
    "hide_framing": "隐藏构图",
    "horizon": "地平线",
    "moon_altitude": "月球高度",
    "object_altitude": "{object}高度",

    // ========================================================================
    // HELP / ABOUT
    // ========================================================================
    "help": "帮助",
    "about": "关于",
    "about_nova_dso_tracker": "关于Nova DSO Tracker",

    // ========================================================================
    // MISC / PLACEHOLDERS
    // ========================================================================
    "select_language": "选择语言",
    "toggle_theme": "切换主题",
    "toggle_dark_light_theme": "切换深色/浅色主题"
};
