# SPDX-License-Identifier: Apache-2.0
"""What UIKit names a tab bar, a toolbar and a navigation bar, in every language iOS speaks.

idb_companion and the native helper read accessibility, which gives a tab bar and a toolbar no role or trait of their
own: each is a group, with no children, that UIKit labels with its name -- "Tab Bar" and "Toolbar" in English,
"Tab-Leiste" and "Symbolleiste" in German, "タブバー" and "ツールバー" in Japanese. A navigation bar is a group whose
role is described as "Nav bar", "Navigationsleiste", "ナビゲーションバー". So a bar is known by its name, in whichever
language the device is in.

The names are UIKit's own: the ``tab.bar.label``, ``toolbar.label`` and ``navbar.role`` strings of
``System/Library/AccessibilityBundles/UIKit.axbundle/*.lproj/Accessibility.strings`` in the iOS 18.6, 26.5 and 27.0
simulator runtimes, every language of each (read 2026-10-16). A release may rename one -- German's tab bar was
"Tableiste" before it was "Tab-Leiste" -- so each release's names are kept.
"""

from __future__ import annotations

#: What UIKit labels a tab bar.
TAB_BAR_NAMES = frozenset({
    "Bar Tab", "Barra de Abas", "Barra de pestanyes", "Barra de pestañas", "Barra de separadores",
    "Barra pannelli", "Barre d’onglets", "Bară de file", "Fanelinje", "Flikfält", "Lapsor", "Lišta s tabmi",
    "Pasek kart", "Sekme Çubuğu", "Skirtukų juosta", "Tab Bar", "Tab-Leiste", "Tabbladbalk", "Tableiste",
    "Thanh tab", "Traka s karticama", "Vrstica z zavihki", "Välilehtipalkki", "Řádek panelů", "Γραμμή καρτελών",
    "Лента за раздели", "Панель вкладок", "Смуга вкладок", "Қойынды жолағы", "שורת הכרטיסיות",
    "شريط علامات التبويب", "ٹیب بار", "टॅब बार", "टैब बार", "ট্যাব বার", "ਟੈਬ ਬਾਰ", "ટૅબ બાર", "ଟାବ୍ ବାର୍",
    "தாவல் பட்டி", "ట్యాబ్ బార్", "ಟ್ಯಾಬ್ ಬಾರ್", "ടാബ് ബാർ", "รายการแถบ", "タブバー", "分頁列", "标签页栏", "標籤頁列", "탭 막대",
})  # fmt: skip
#: What UIKit labels a toolbar.
TOOLBAR_NAMES = frozenset({
    "Alatna traka", "Araç Çubuğu", "Bar Alat", "Barra de Ferramentas", "Barra de ferramentas",
    "Barra de herramientas", "Barra d’eines", "Barra strumenti", "Barre d’outils", "Bară de instrumente",
    "Eszközsor", "Knoppenbalk", "Orodna vrstica", "Panel nástrojů", "Panel s nástrojmi", "Pasek narzędzi",
    "Symbolleiste", "Thanh công cụ", "Toolbar", "Työkalupalkki", "Verktygsfält", "Verktøylinje", "Værktøjslinje",
    "Įrankių juosta", "Γραμμή εργαλείων", "Лента с инструменти", "Панель инструментов", "Панель інструментів",
    "Құралдар тақтасы", "סרגל כלים", "شريط الأدوات", "ٹول بار", "टूलबार", "টুলবার", "ਟੂਲਬਾਰ", "ટૂલબાર",
    "ଟୁଲ୍\u200cବାର୍", "ଟୁଲ୍\u200cବାର୍\u200c", "டூல்பார்", "టూల్\u200cబార్", "ಟೂಲ್\u200cಬಾರ್", "ടൂൾബാർ",
    "แถบเครื่องมือ", "ツールバー", "工具列", "工具栏", "도구 막대",
})  # fmt: skip
#: What UIKit describes a navigation bar's role as.
NAVIGATION_BAR_ROLES = frozenset({
    "Bar nav", "Baris nav", "Barra de navegació", "Barra de navegación", "Barra de navegação",
    "Barra navigazione", "Barre de navigation", "Bară de navigare", "Dolaşma çubuğu", "Naršymo juosta", "Nav bar",
    "Nav traka", "Nav.linje", "Navigatiebalk", "Navigationsleiste", "Navigačná lišta", "Navigační panel",
    "Navigeringsfält", "Navigeringslinje", "Navigációs sáv", "Pasek nawigacji", "Selauspalkki",
    "Thanh điều hướng", "Vrstica za krmarjenje", "Γραμμή πλοήγησης", "Лента за навигация", "Навигационная панель",
    "Навигациялық жолақ", "Навігаційна панель", "שורת ניווט", "شريط التنقل", "نیو بار", "नॅव्ह बार", "नैव बार",
    "ন্যাভ বার", "ਨਵ ਬਾਰ", "નૅવિગેશન બાર", "ନାଭ୍ ବାର୍", "வழிசெலுத்தல் பார்", "నావిగేషన్ బార్", "ನ್ಯಾವಿಗೇಷನ್ ಬಾರ್",
    "നവ് ബാർ", "แถบนำทาง", "ナビゲーションバー", "导航栏", "導覽列", "탐색 막대",
})  # fmt: skip
