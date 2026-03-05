/**
 * Japanese translations for Nova DSO Tracker
 */
window.NOVA_I18N = window.NOVA_I18N || {};
window.NOVA_I18N.ja = {
    // ========================================================================
    // COMMON / GENERAL
    // ========================================================================
    "loading": "読み込み中...",
    "calculating": "計算中...",
    "calculating_month": "{1}ヶ月中{0}ヶ月目を計算中...",
    "error": "エラー",
    "success": "成功",
    "cancel": "キャンセル",
    "save": "保存",
    "delete": "削除",
    "edit": "編集",
    "close": "閉じる",
    "confirm": "確認",
    "yes": "はい",
    "no": "いいえ",
    "name": "名前",
    "description": "説明",
    "notes": "メモ",
    "date": "日付",
    "time": "時刻",
    "na": "N/A",

    // ========================================================================
    // DASHBOARD
    // ========================================================================
    "dashboard": "ダッシュボード",
    "objects": "天体",
    "journal": "観測日誌",
    "heatmap": "ヒートマップ",
    "outlook": "展望",
    "inspiration": "インスピレーション",

    // Saved Views
    "saved_views": "保存したビュー",
    "saved_views_placeholder": "-- 保存したビュー --",
    "error_loading_views": "ビューの読み込みエラー",
    "name_required": "名前は必須です",
    "error_saving_view": "ビューの保存エラー: {error}",
    "error_deleting_view": "ビューの削除エラー: {error}",
    "confirm_delete_view": "ビュー「{name}」を削除してもよろしいですか？",
    "error_load_view_data": "エラー: キャッシュからビューデータを読み込めませんでした。",

    // Simulation Mode
    "simulation": "シミュレーション",
    "simulation_mode": "シミュレーションモード",
    "simulated": "シミュレート済み",
    "mode": "モード",
    "update": "更新",

    // Data Loading
    "data_load_failed": "データの読み込みに失敗しました: {error}",

    // ========================================================================
    // OBJECT TABLE
    // ========================================================================
    "object": "天体",
    "common_name": "通称",
    "constellation": "星座",
    "type": "タイプ",
    "magnitude": "等級",
    "altitude": "高度",
    "azimuth": "方位角",
    "transit_time": "南中時刻",
    "observable_duration": "観測可能時間",
    "max_altitude": "最大高度",
    "moon_separation": "月離角",
    "trend": "トレンド",
    "sb": "SB",
    "size": "サイズ",
    "best_month": "最適月",
    "current": "現在",
    "local_time": "現地時間",
    "minutes": "分",

    // Status Strip
    "location": "場所",
    "moon": "月",
    "dusk": "薄暮",
    "dawn": "黎明",

    // ========================================================================
    // GRAPH VIEW / OBJECT DETAIL
    // ========================================================================
    "sep": "離角",
    "failed_update_active_project": "アクティブプロジェクトの更新に失敗しました: {error}",
    "successfully_updated_active_project": "{object}のアクティブプロジェクトステータスを{status}に正常に更新しました",
    "simbad_requires_internet": "SIMBADはデータを読み込むためにアクティブなインターネット接続が必要です。",
    "simbad_requires_internet_short": "SIMBADはアクティブなインターネット接続が必要です。",
    "no_imaging_opportunities": "検索条件内で良い imaging 機会が見つかりませんでした。",
    "error_loading_opportunities": "機会の読み込みエラー: {error}",
    "failed_load_opportunities": "imaging 機会の読み込みに失敗しました。詳細はコンソールを参照してください。({error})",
    "add_to_calendar": "カレンダーに追加",
    "view_inspiration": "インスピレーションを見る",
    "add_own_inspiration": "自分のインスピレーションを追加しよう！",
    "seeing_automated_survey_images": "現在、自動化されたサーベイ画像（DSS2）が表示されています。",
    "display_own_astrophotos": "ご存知でしたか？ここで自分の天体写真やお気に入りの参考画像を表示できます。",
    "go_to_config_manage_objects": "<strong>設定 &gt; オブジェクトの管理</strong> に移動し、ターゲットに「インスピレーションコンテンツ」（画像URL、クレジット、説明文）を追加してください。",

    // ========================================================================
    // OBJECTS SECTION
    // ========================================================================
    "showing_objects": "{count}個の天体を表示",
    "showing_objects_of": "{total}個中{visible}個の天体を表示",
    "no_objects_selected": "天体が選択されていません。",
    "confirm_bulk_action": "{count}個の天体を{action}してもよろしいですか？",
    "bulk_action_failed": "一括操作に失敗しました。コンソールを参照してください。",
    "bulk_fetch_details_failed": "詳細の一括取得に失敗しました。コンソールを参照してください。",
    "fetching_details_for": "{count}個の天体の詳細を取得中...",
    "no_potential_duplicates": "座標に基づく潜在的な重複は見つかりませんでした。",
    "all_duplicates_resolved": "すべての重複が解決されました！",
    "error_scanning_duplicates": "重複のスキャンエラー。",
    "merge_confirm": "「{merge}」を「{keep}」にマージしますか？\n\nこれにより：\n1. {merge}から{keep}へのジャーナル/プロジェクトの再リンク\n2. {merge}からメモをコピー\n3. {merge}を永久に削除",
    "keep_a_merge_b": "Aを保持、Bをマージ",
    "keep_b_merge_a": "Bを保持、Aをマージ",
    "no_telescopes": "望遠鏡が定義されていません。",
    "no_cameras": "カメラが定義されていません。",
    "no_reducers": "レデューサーが定義されていません。",
    "no_rigs": "構成が定義されていません。",
    "selected": "選択済み",
    "please_enter_object_id": "天体IDを入力してください。",
    "checking_local_library": "{name}のローカルライブラリを確認中...",
    "object_found_library": "天体「{name}」がライブラリで見つかりました。編集用に読み込み中。",
    "object_not_found_simbad": "ローカルライブラリで天体が見つかりませんでした。SIMBADを確認中...",
    "found_details_loaded": "見つかりました: {name}。SIMBADから詳細を読み込みました。",
    "error_fetching_simbad": "エラー: {error}。\n天体を手動で追加し、「確認」をクリックできます。",
    "warning_ra_degrees": "警告: RA値({ra})は> 24で、度数を意味します。\n\n自動的に{corrected}時間に変換しますか？",
    "importing_catalog": "「{name}」をインポート中...\n\nこれにより、サーバーからのデータでライブラリが更新されます：\n• このパックの新しい天体が追加されます。\n• 既存の天体は最新の画像/説明で更新されます。\n• 個人のプロジェクトメモ、ステータス、フレーミングは安全に保持されます。\n\n続行しますか？",

    // ========================================================================
    // CONFIG FORM
    // ========================================================================
    "update_component": "コンポーネントを更新",
    "update_rig": "構成を更新",
    "confirm_delete_component": "コンポーネントの削除は永続的で、元に戻すことはできません。よろしいですか？",
    "confirm_delete_rig": "構成「{name}」を削除してもよろしいですか？",
    "select_telescope": "-- 望遠鏡を選択 --",
    "select_camera": "-- カメラを選択 --",
    "none": "-- なし --",
    "telescope": "望遠鏡",
    "camera": "カメラ",
    "reducer_extender": "レデューサー/エクステンダー",
    "guiding": "ガイド",
    "owner": "オーナー",
    "import": "インポート",
    "imported": "インポート済み",
    "importing": "インポート中...",
    "confirm_import_item": "この{type}をインポートしてもよろしいですか？",
    "import_failed": "インポートに失敗しました。詳細はコンソールを参照してください。",
    "no_shared_objects": "他のユーザーからの共有天体が見つかりません。",
    "no_shared_components": "他のユーザーからの共有コンポーネントが見つかりません。",
    "no_shared_views": "他のユーザーからの共有ビューが見つかりません。",
    "error_loading_shared": "共有アイテムの読み込みエラー。",
    "view": "表示",
    "saving": "保存中...",
    "saved": "保存しました！",
    "error_saving": "保存エラー: {error}",
    "network_error_saving": "天体の保存中にネットワークエラーが発生しました。",
    "connecting": "接続中...",
    "importing_please_wait": "{entity}をインポート中、お待ちください...",
    "import_failed_server": "インポートに失敗しました: サーバーがステータス{status}を返しました",
    "upload_error": "アップロードエラー: {error}",
    "upload_failed": "アップロードに失敗しました。コンソールを参照してください。",
    "done": "完了",
    "shared_notes_for": "{name}の共有メモ",
    "confirm_fetch_details": "これにより、すべての天体がスキャンされ、外部データベースから不足している詳細（タイプ、等級、サイズなど）が取得されます。\n\nライブラリのサイズによっては、少し時間がかかる場合があります。\n\n続行しますか？",
    "connection_lost_refreshing": "接続が失われました。ページを更新中...",
    "error_preparing_print": "印刷ビューの準備エラー: {error}",

    // Sampling
    "oversampled": "オーバーサンプリング",
    "slightly_oversampled": "わずかにオーバーサンプリング",
    "good_sampling": "良好なサンプリング",
    "effective_fl": "有効焦点距離",
    "image_scale": "画像スケール",
    "field_of_view": "視野",
    "guiding": "ガイド",
    "slightly_undersampled": "わずかにアンダーサンプリング",
    "undersampled": "アンダーサンプリング",
    "px_fwhm": "px/FWHM",
    "tip_binning": "ヒント: 2x2ビニングで約{scale}\"/px ({sampling} px/FWHM)になります",
    "check_software_max": " — ソフトウェアの最大値を確認してください",

    // ========================================================================
    // JOURNAL SECTION
    // ========================================================================
    "report_frame_not_found": "レポートフレームが見つかりません。",
    "preparing_print_view": "印刷ビューを準備中...",
    "merging_session": "セッション {current}/{total} をマージ中...",
    "check_popup": "ポップアップを確認...",
    "appendix_session": "付録: セッション {number}",
    "add_new_session": "新しいセッションを追加",
    "add_session": "セッションを追加",
    "save_changes": "変更を保存",
    "editing_session": "セッションを編集中: {date}",
    "visibility": "可視性: {date}",
    "altitude_deg": "高度 (°)",
    "recommendation": "推奨: {pixels} px",
    "time_limit_reached": "(時間制限に到達)",
    "max_real_subs": "(最大 {max} | 実際 {real})",

    // ========================================================================
    // BASE JS / GLOBAL
    // ========================================================================
    "loading_help_content": "ヘルプコンテンツを読み込み中...",
    "help_content_empty": "エラー: ヘルプコンテンツが空です。",
    "network_error_help": "ネットワークエラー: ヘルプトピック「{topic}」を読み込めませんでした。",
    "latest_version": "最新バージョン: v{version}",

    // ========================================================================
    // MESSAGES / ALERTS
    // ========================================================================
    "saved_successfully": "正常に保存されました",
    "deleted_successfully": "正常に削除されました",
    "confirm_delete": "これを削除してもよろしいですか？",
    "unsaved_changes": "未保存の変更があります。ページを離れますか？",
    "network_error": "ネットワークエラー。もう一度お試しください。",
    "session_expired": "セッションが期限切れです。再度ログインしてください。",
    "error_with_message": "エラー: {message}",
    "failed_with_error": "失敗: {error}",

    // ========================================================================
    // CHART / GRAPH
    // ========================================================================
    "altitude_chart": "高度チャート",
    "altitude_degrees": "高度 (°)",
    "time_hours": "時間",
    "tonight": "今夜",
    "show_framing": "構図を表示",
    "hide_framing": "構図を非表示",
    "horizon": "地平線",
    "moon_altitude": "月の高度",
    "object_altitude": "{object}の高度",

    // ========================================================================
    // HELP / ABOUT
    // ========================================================================
    "help": "ヘルプ",
    "about": "概要",
    "about_nova_dso_tracker": "Nova DSO Trackerについて",

    // ========================================================================
    // MISC / PLACEHOLDERS
    // ========================================================================
    "select_language": "言語を選択",
    "toggle_theme": "テーマを切り替え",
    "toggle_dark_light_theme": "ダーク/ライトテーマを切り替え"
};
