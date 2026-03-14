#!/usr/bin/env python3
"""Fix go_to_config_manage_objects translations with fully translated strings."""

import polib

CORRECTIONS = {
    "de": "Gehen Sie zu %(strong_start)sKonfiguration > Objekte verwalten%(strong_end)s, um \"Inspirationsinhalt\" (Bild-URLs, Credits, Beschreibungen) zu Ihren Zielen hinzuzufügen.",
    "fr": "Allez dans %(strong_start)sConfiguration > Gérer les objets%(strong_end)s pour ajouter du \"Contenu d'inspiration\" (URL des images, crédits, descriptions) à vos cibles.",
    "es": "Vaya a %(strong_start)sConfiguración > Gestionar objetos%(strong_end)s para añadir \"Contenido de inspiración\" (URLs de imágenes, créditos, descripciones) a sus objetivos.",
    "ja": "%(strong_start)s設定 > オブジェクト管理%(strong_end)sにアクセスして、ターゲットに「インスピレーションコンテンツ」（画像URL、クレジット、説明文）を追加してください。",
    "zh": "前往%(strong_start)s配置 > 管理对象%(strong_end)s，为您的目标添加「灵感内容」（图片URL、来源、描述）。",
}

def fix_translations():
    for lang, correct_msgstr in CORRECTIONS.items():
        po_path = f"translations/{lang}/LC_MESSAGES/messages.po"
        print(f"Fixing {lang}...")

        po = polib.pofile(po_path)
        found = False

        for entry in po:
            if 'strong_start' in entry.msgid and 'Configuration' in entry.msgid and 'Manage Objects' in entry.msgid:
                entry.msgstr = correct_msgstr
                print(f"  Updated msgstr")
                found = True
                break

        if not found:
            print(f"  WARNING: Entry not found!")
            continue

        po.save(po_path)
        print(f"  Saved")

    print("\nDone!")

if __name__ == "__main__":
    fix_translations()
