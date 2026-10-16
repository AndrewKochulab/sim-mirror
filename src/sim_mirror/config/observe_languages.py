# SPDX-License-Identifier: Apache-2.0
"""What observe mode keeps agents off in languages besides English: ``agent.observe_languages``.

An app shown in German says "Löschen" where one in English says "Delete", and SimMirror cannot tell which language an
app is shown in: a device's own language is not the app's when a scheme launches it in another. So the words of every
language here are matched at once, beside `agent.observe_words`, unless a person narrows the list.

Each language has the words its buttons use to delete, remove, reset, sign out, disconnect, save, apply, send, confirm,
buy, pay, subscribe and transfer -- chosen by hand, not by a translator, and kept to words that say little else.
A word that is also an everyday one is left out where it would refuse what only looks: Chinese 应用 is "apply" and
also "app", Japanese 確認 is "confirm" and also "check", Korean 확인 is "OK". Scripts written without spaces are matched
anywhere in a label (`core.refusals`), so 保存 refuses "保存された写真" too.
"""

from __future__ import annotations

#: Each language's words, separated by commas as `agent.observe_words` writes them.
WORDS: dict[str, str] = {
    "de": (
        "löschen, entfernen, zurücksetzen, abmelden, ausloggen, trennen, vergessen, deaktivieren, speichern, sichern, "
        "übernehmen, anwenden, senden, absenden, bestätigen, kaufen, bezahlen, abonnieren, kündigen, überweisen"
    ),
    "es": (
        "eliminar, borrar, quitar, restablecer, cerrar sesión, desconectar, desvincular, olvidar, desactivar, guardar, "
        "aplicar, enviar, confirmar, comprar, pagar, suscribirse, cancelar suscripción, darse de baja, transferir"
    ),
    "fr": (
        "supprimer, effacer, retirer, réinitialiser, déconnecter, déconnexion, dissocier, oublier, désactiver, "
        "enregistrer, appliquer, envoyer, valider, confirmer, acheter, payer, abonner, désabonner, transférer"
    ),
    "it": (
        "elimina, eliminare, cancella, rimuovi, ripristina, inizializza, esci, disconnetti, dissocia, dimentica, "
        "disattiva, salva, applica, invia, conferma, acquista, compra, paga, abbonati, annulla iscrizione, trasferisci"
    ),
    "ja": (
        "削除, 消去, 取り除, リセット, 初期化, ログアウト, サインアウト, 接続解除, 登録解除, "
        "保存, 適用, 送信, 購入, 支払, 解約"
    ),
    "ko": (
        "삭제, 삭제하기, 제거, 지우기, 재설정, 초기화, 로그아웃, 연결 해제, 저장, 저장하기, 적용, 보내기, 전송, 구매, "
        "구매하기, 결제, 결제하기, 구독, 구독하기, 구독 취소, 송금"
    ),
    "nl": (
        "verwijder, verwijderen, wis, wissen, herstel, stel opnieuw in, afmelden, uitloggen, loskoppelen, vergeet, "
        "bewaar, opslaan, toepassen, verstuur, versturen, verzend, verzenden, bevestig, bevestigen, koop, kopen, "
        "betaal, betalen, abonneer, abonneren, overmaken"
    ),
    "pl": (
        "usuń, usunąć, wymaż, resetuj, wyzeruj, wyloguj, rozłącz, odłącz, zapomnij, dezaktywuj, zapisz, zastosuj, "
        "wyślij, potwierdź, kup, zapłać, subskrybuj, anuluj subskrypcję, przelew, przelej"
    ),
    "pt": (
        "apagar, excluir, eliminar, remover, redefinir, repor, sair, terminar sessão, desconectar, desassociar, "
        "esquecer, desativar, salvar, guardar, aplicar, enviar, confirmar, comprar, pagar, assinar, "
        "cancelar assinatura, transferir"
    ),
    "ru": (
        "удалить, стереть, сбросить, выйти, отключить, отвязать, забыть, деактивировать, сохранить, применить, "
        "отправить, подтвердить, купить, оплатить, подписаться, отписаться, перевести"
    ),
    "tr": (
        "sil, kaldır, sıfırla, çıkış yap, oturumu kapat, bağlantıyı kes, unut, devre dışı bırak, kaydet, uygula, "
        "gönder, onayla, satın al, öde, abone ol, aboneliği iptal et"
    ),
    "uk": (
        "видалити, стерти, скинути, вийти, відключити, від'єднати, від’єднати, забути, деактивувати, зберегти, "
        "застосувати, надіслати, відправити, підтвердити, купити, оплатити, сплатити, підписатися, відписатися, "
        "переказати"
    ),
    "zh": (
        "删除, 刪除, 移除, 抹掉, 抹除, 还原, 還原, 重置, 重設, 退出登录, 退出登入, 登出, 注销, 註銷, 断开, 斷開, 保存, "
        "储存, 儲存, 发送, 發送, 傳送, 购买, 購買, 支付, 付款, 订阅, 訂閱, 转账, 轉帳"
    ),
}
#: Every language there are words for, as `agent.observe_languages` names them by default.
LANGUAGES = ", ".join(WORDS)
#: What `agent.observe_languages` may name: those, and English -- whose words are `agent.observe_words`, so it adds
#: none.
KNOWN = frozenset({"en", *WORDS})


def base(code: str) -> str:
    """The language a code names, less its script and region: ``zh-Hant-TW`` is ``zh``."""
    return code.strip().split("-")[0].casefold()


def words_in(languages: str) -> str:
    """The words of the languages a list of codes names, separated by commas; a language with none adds nothing."""
    return ", ".join(
        WORDS[language] for language in dict.fromkeys(map(base, languages.split(","))) if language in WORDS
    )
