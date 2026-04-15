#!/usr/bin/env python3
"""Translate identical and fuzzy entries in .po files for de, fr, ja."""

import polib
from pathlib import Path

# Technical terms to leave untranslated
KEEP_AS_IS = {
    'SQM', 'RA', 'Dec', 'FWHM', 'RMS', 'PHD2', 'ASIAIR', 'NINA', 'OAG',
    'Darks', 'Flats', 'Bias', 'SIMBAD', 'HFR', 'Dithering', 'FITS', 'PNG',
    'CSV', 'JSON', 'RGB', 'Ha', 'OIII', 'SII', 'PA'
}

# Translations for identical entries (msgid == msgstr)
IDENTICAL_TRANSLATIONS = {
    'de': {
        'Name': 'Name',
        'Status': 'Status',
        'Import': 'Importieren',
        'Max Alt (°)': 'Max Höhe (°)',
        'Status:': 'Status:',
        'Admin': 'Admin',
        'Version': 'Version',
        'Built by Anton Gutscher': 'Erstellt von Anton Gutscher',
        'Name:': 'Name:',
        'Name (A→Z)': 'Name (A→Z)',
        'Name (Z→A)': 'Name (Z→A)',
        'Inspiration': 'Inspiration',
        'Geo Belt': 'Geo-Gürtel',
        'Rotation (PA)': 'Rotation (PA)',
        'Gamma': 'Gamma',
        'Dashboard': 'Dashboard',
        'Simulation': 'Simulation',
        'Position': 'Position',
        'Heatmap': 'Heatmap',
        'Trend': 'Trend',
        'Transit': 'Kulmination',
        'SB': 'SF',
        'Max Alt': 'Max Höhe',
        'Integration': 'Integration',
        'Binning:': 'Binning:',
        'Filter:': 'Filter:',
        'DSO Tracker': 'DSO-Tracker',
        'Filter': 'Filter',
        'Nova Pocket': 'Nova Pocket',
        'Max Alt:': 'Max Höhe:',
        'Flips': 'Meridianflips',
        'Autocenter': 'Autozentrierung',
        'Integration:': 'Integration:',
    },
    'fr': {
        'Constellation': 'Constellation',
        'Multiple': 'Multiple',
        'Type': 'Type',
        'Source': 'Source',
        'Description': 'Description',
        'Date (UTC)': 'Date (UTC)',
        'Actions': 'Actions',
        'Version': 'Version',
        'Nova – Configuration': 'Nova – Configuration',
        'Configuration': 'Configuration',
        'Journal': 'Journal',
        'Inspiration': 'Inspiration',
        'Rotation (PA)': 'Rotation (PA)',
        'Saturation': 'Saturation',
        'Simulation': 'Simulation',
        'Position': 'Position',
        'Altitude': 'Altitude',
        'Transit': 'Culmination',
        'Observable': 'Observable',
        'minutes': 'minutes',
        'observable (°)': 'observable (°)',
        'Magnitude': 'Magnitude',
        'SB': 'SB',
        'Altitude °': 'Altitude °',
        'Angle': 'Angle',
        'Points': 'Points',
        'Distance': 'Distance',
        'Nova Pocket': 'Nova Pocket',
        'Options': 'Options',
        '%(prog)s, version %(version)s': '%(prog)s, version %(version)s',
    },
    'ja': {
        'SB': '表面輝度',
        'env var: {var}': '環境変数: {var}',
    },
}

# Translations for fuzzy entries
FUZZY_TRANSLATIONS = {
    'de': {
        'Transit Time': 'Kulminationszeit',
        'Source: Wiki': 'Quelle: Wiki',
        'View Charts': 'Diagramme anzeigen',
        'Inspiration Content (Optional)': 'Inspiration-Inhalt (Optional)',
        'Text Credit': 'Textquelle',
        'Search': 'Suchen',
        'Private Notes:': 'Private Notizen:',
        'Personal notes, framing ideas... (not shared)': 'Persönliche Notizen, Framing-Ideen... (nicht geteilt)',
        'Shared Notes:': 'Geteilte Notizen:',
        'Public notes, acquisition advice... (will be shared)': 'Öffentliche Notizen, Aufnahme-Tipps... (wird geteilt)',
        'Catalog Packs': 'Katalog-Pakete',
        'Tags': 'Schlagwörter',
        'Action': 'Aktion',
        'Potential Duplicates': 'Potentielle Duplikate',
        'Moon Illum (%%)': 'Mondbeleuchtung (%%)',
        'Min Observable (min):': 'Min beobachtbar (min):',
        'Min Max Altitude (°):': 'Min Max-Höhe (°):',
        'Max Moon Illum (%%):': 'Max Mondbeleuchtung (%%):',
        'Min Moon Sep (°):': 'Min Mondabstand (°):',
        'Search months:': 'Monate suchen:',
        'Save Settings': 'Einstellungen speichern',
        'Locations Configuration': 'Standort-Konfiguration',
        'Add New Location': 'Neuen Standort hinzufügen',
        'Lat:': 'Breite:',
        'Lon:': 'Länge:',
        'Timezone:': 'Zeitzone:',
        'Active:': 'Aktiv:',
        'Import .hzn': '.hzn importieren',
        'Comments:': 'Kommentare:',
        'Del:': 'Löschen:',
        'Update Locations': 'Standorte aktualisieren',
        'Help with Rigs': 'Hilfe zu Rigs',
        'Telescopes': 'Teleskope',
        'Add Telescope': 'Teleskop hinzufügen',
        'Cameras': 'Kameras',
        'Sensor Height (mm):': 'Sensorhöhe (mm):',
        'Add Camera': 'Kamera hinzufügen',
        'Reducers / Extenders': 'Reducer/Extender',
        'Factor:': 'Faktor:',
        'Add Reducer/Extender': 'Reducer/Extender hinzufügen',
        'Rig Name:': 'Rig-Name:',
        'Guide Optics': 'Guiding-Optik',
        'Guide Scope:': 'Guiding-Teleskop:',
        'Guide Camera:': 'Guiding-Kamera:',
        'Create Rig': 'Rig erstellen',
        'Good Seeing (2.0" - 4.0" FWHM)': 'Gutes Seeing (2.0" - 4.0" FWHM)',
        'Poor Seeing (> 4.0" FWHM)': 'Schlechtes Seeing (> 4.0" FWHM)',
        'Existing Rigs': 'Vorhandene Rigs',
        'Shared Items from Other Users': 'Geteilte Elemente anderer Benutzer',
        'Help with Shared Items': 'Hilfe zu geteilten Elementen',
        'Shared Objects': 'Geteilte Objekte',
        'Img': 'Bild',
        'Shared By': 'Geteilt von',
        'Filter ID...': 'ID filtern...',
        'Filter name...': 'Name filtern...',
        'Filter type...': 'Typ filtern...',
        'Filter con...': 'Kon filtern...',
        'Filter user...': 'Benutzer filtern...',
        'Not Imported': 'Nicht importiert',
        'Shared Views': 'Geteilte Ansichten',
        'View Name': 'Ansichtsname',
        'Filter desc...': 'Beschreibung filtern...',
        'Shared Components': 'Geteilte Komponenten',
        'Component Name': 'Komponentenname',
        'All Types': 'Alle Typen',
        'Reducer/Extender': 'Reducer/Extender',
        'Fetching Details...': 'Details werden geladen...',
        'Overlap %%': 'Überlappung %%',
        'Moon Illumination at Session (%%):': 'Mondbeleuchtung bei Sitzung (%%):',
        'Searching for %%(name)s...': 'Suche nach %%(name)s...',
        'Calculated %%(count)d of %%(total)d objects...': 'Berechnet %%(count)d von %%(total)d Objekten...',
        'Usage:': 'Verwendung:',
    },
    'fr': {
        'Transit Time': 'Heure de culmination',
        'Source: Wiki': 'Source : Wiki',
        'View Charts': 'Voir les graphiques',
        'Inspiration Content (Optional)': "Contenu d'inspiration (optionnel)",
        'Text Credit': 'Crédit texte',
        'Search': 'Rechercher',
        'Private Notes:': 'Notes privées :',
        'Personal notes, framing ideas... (not shared)': 'Notes personnelles, idées de cadrage... (non partagées)',
        'Shared Notes:': 'Notes partagées :',
        'Public notes, acquisition advice... (will be shared)': 'Notes publiques, conseils d\'acquisition... (seront partagées)',
        'Catalog Packs': 'Packs de catalogue',
        'Tags': 'Étiquettes',
        'Action': 'Action',
        'Potential Duplicates': 'Doublons potentiels',
        'Moon Illum (%%)': 'Illumination lunaire (%%)',
        'Min Observable (min):': 'Min observable (min) :',
        'Min Max Altitude (°):': 'Altitude max min (°) :',
        'Max Moon Illum (%%):': 'Max illumination lunaire (%%) :',
        'Min Moon Sep (°):': 'Min séparation lunaire (°) :',
        'Search months:': 'Rechercher mois :',
        'Save Settings': 'Enregistrer les paramètres',
        'Locations Configuration': 'Configuration des lieux',
        'Add New Location': 'Ajouter un nouveau lieu',
        'Lat:': 'Lat :',
        'Lon:': 'Lon :',
        'Timezone:': 'Fuseau horaire :',
        'Active:': 'Actif :',
        'Import .hzn': 'Importer .hzn',
        'Comments:': 'Commentaires :',
        'Del:': 'Suppr :',
        'Update Locations': 'Mettre à jour les lieux',
        'Help with Rigs': 'Aide pour les rigs',
        'Telescopes': 'Télescopes',
        'Add Telescope': 'Ajouter un télescope',
        'Cameras': 'Caméras',
        'Sensor Height (mm):': 'Hauteur du capteur (mm) :',
        'Add Camera': 'Ajouter une caméra',
        'Reducers / Extenders': 'Réducteurs / Extendeurs',
        'Factor:': 'Facteur :',
        'Add Reducer/Extender': 'Ajouter réducteur/extendeur',
        'Rig Name:': 'Nom du rig :',
        'Guide Optics': 'Optique de guidage',
        'Guide Scope:': 'Lunette de guidage :',
        'Guide Camera:': 'Caméra de guidage :',
        'Create Rig': 'Créer un rig',
        'Good Seeing (2.0" - 4.0" FWHM)': 'Bon seeing (2.0" - 4.0" FWHM)',
        'Poor Seeing (> 4.0" FWHM)': 'Mauvais seeing (> 4.0" FWHM)',
        'Existing Rigs': 'Rigs existants',
        'Shared Items from Other Users': 'Éléments partagés par d\'autres utilisateurs',
        'Help with Shared Items': 'Aide pour les éléments partagés',
        'Shared Objects': 'Objets partagés',
        'Img': 'Img',
        'Shared By': 'Partagé par',
        'Filter ID...': 'Filtrer ID...',
        'Filter name...': 'Filtrer nom...',
        'Filter type...': 'Filtrer type...',
        'Filter con...': 'Filtrer con...',
        'Filter user...': 'Filtrer utilisateur...',
        'Not Imported': 'Non importé',
        'Shared Views': 'Vues partagées',
        'View Name': 'Nom de la vue',
        'Filter desc...': 'Filtrer desc...',
        'Shared Components': 'Composants partagés',
        'Component Name': 'Nom du composant',
        'All Types': 'Tous les types',
        'Reducer/Extender': 'Réducteur/Extendeur',
        'Fetching Details...': 'Récupération des détails...',
        'Overlap %%': 'Chevauchement %%',
        'Moon Illumination at Session (%%):': 'Illumination lunaire à la session (%%) :',
        'Searching for %%(name)s...': 'Recherche de %%(name)s...',
        'Calculated %%(count)d of %%(total)d objects...': 'Calculé %%(count)d sur %%(total)d objets...',
        'Usage:': 'Utilisation :',
    },
    'ja': {
        'Transit Time': '南中時刻',
        'Source: Wiki': '出典: Wiki',
        'View Charts': 'チャートを見る',
        'Inspiration Content (Optional)': 'インスピレーションコンテンツ（任意）',
        'Text Credit': 'テキストクレジット',
        'Search': '検索',
        'Private Notes:': 'プライベートメモ:',
        'Personal notes, framing ideas... (not shared)': '個人メモ、構図アイデア...（非共有）',
        'Shared Notes:': '共有メモ:',
        'Public notes, acquisition advice... (will be shared)': '公開メモ、撮影アドバイス...（共有されます）',
        'Catalog Packs': 'カタログパック',
        'Tags': 'タグ',
        'Action': 'アクション',
        'Potential Duplicates': '重複の可能性',
        'Moon Illum (%%)': '月光照度 (%%)',
        'Min Observable (min):': '最小観測可能時間 (分):',
        'Min Max Altitude (°):': '最小最大高度 (°):',
        'Max Moon Illum (%%):': '最大月光照度 (%%):',
        'Min Moon Sep (°):': '最小月間隔 (°):',
        'Search months:': '月を検索:',
        'Save Settings': '設定を保存',
        'Locations Configuration': '場所の設定',
        'Add New Location': '新しい場所を追加',
        'Lat:': '緯度:',
        'Lon:': '経度:',
        'Timezone:': 'タイムゾーン:',
        'Active:': '有効:',
        'Import .hzn': '.hznをインポート',
        'Comments:': 'コメント:',
        'Del:': '削除:',
        'Update Locations': '場所を更新',
        'Help with Rigs': 'リグのヘルプ',
        'Telescopes': '望遠鏡',
        'Add Telescope': '望遠鏡を追加',
        'Cameras': 'カメラ',
        'Sensor Height (mm):': 'センサー高さ (mm):',
        'Add Camera': 'カメラを追加',
        'Reducers / Extenders': 'レデューサー/エクステンダー',
        'Factor:': '倍率:',
        'Add Reducer/Extender': 'レデューサー/エクステンダーを追加',
        'Rig Name:': 'リグ名:',
        'Guide Optics': 'ガイド光学系',
        'Guide Scope:': 'ガイドスコープ:',
        'Guide Camera:': 'ガイドカメラ:',
        'Create Rig': 'リグを作成',
        'Good Seeing (2.0" - 4.0" FWHM)': '良いシーン (2.0" - 4.0" FWHM)',
        'Poor Seeing (> 4.0" FWHM)': '悪いシーン (> 4.0" FWHM)',
        'Existing Rigs': '既存のリグ',
        'Shared Items from Other Users': '他のユーザーからの共有アイテム',
        'Help with Shared Items': '共有アイテムのヘルプ',
        'Shared Objects': '共有オブジェクト',
        'Img': '画像',
        'Shared By': '共有者',
        'Filter ID...': 'IDでフィルター...',
        'Filter name...': '名前でフィルター...',
        'Filter type...': 'タイプでフィルター...',
        'Filter con...': '星座でフィルター...',
        'Filter user...': 'ユーザーでフィルター...',
        'Not Imported': '未インポート',
        'Shared Views': '共有ビュー',
        'View Name': 'ビュー名',
        'Filter desc...': '説明でフィルター...',
        'Shared Components': '共有コンポーネント',
        'Component Name': 'コンポーネント名',
        'All Types': 'すべてのタイプ',
        'Reducer/Extender': 'レデューサー/エクステンダー',
        'Fetching Details...': '詳細を取得中...',
        'Overlap %%': 'オーバーラップ %%',
        'Moon Illumination at Session (%%):': 'セッション時の月光照度 (%%):',
        'Searching for %%(name)s...': '%%(name)sを検索中...',
        'Calculated %%(count)d of %%(total)d objects...': '%%(total)d個中%%(count)d個のオブジェクトを計算済み...',
        'Usage:': '使用法:',
    },
}


def should_translate(msgid):
    """Check if msgid should be translated (not a technical term only)."""
    stripped = msgid.strip()
    if stripped in KEEP_AS_IS:
        return False
    words = stripped.replace(':', '').replace('(', '').replace(')', '').split()
    if all(w in KEEP_AS_IS for w in words if w):
        return False
    return True


def process_language(lang):
    """Process a single language file."""
    po_path = Path(f'translations/{lang}/LC_MESSAGES/messages.po')
    po = polib.pofile(str(po_path))

    identical_fixed = 0
    fuzzy_fixed = 0
    identical_trans = IDENTICAL_TRANSLATIONS.get(lang, {})
    fuzzy_trans = FUZZY_TRANSLATIONS.get(lang, {})

    for entry in po:
        # Fix identical entries
        if entry.msgid and entry.msgstr == entry.msgid and should_translate(entry.msgid):
            if entry.msgid in identical_trans:
                entry.msgstr = identical_trans[entry.msgid]
                identical_fixed += 1

        # Fix fuzzy entries
        if 'fuzzy' in entry.flags:
            msgid = entry.msgid
            if msgid in fuzzy_trans:
                entry.msgstr = fuzzy_trans[msgid]
                entry.flags.remove('fuzzy')
                fuzzy_fixed += 1

    if identical_fixed > 0 or fuzzy_fixed > 0:
        po.save()

    return identical_fixed, fuzzy_fixed


def main():
    results = {}
    for lang in ['de', 'fr', 'ja']:
        identical, fuzzy = process_language(lang)
        results[lang] = (identical, fuzzy)
        print(f"{lang}: {identical} identical fixed, {fuzzy} fuzzy fixed")

    return results


if __name__ == '__main__':
    main()
