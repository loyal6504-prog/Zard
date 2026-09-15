"""
One-off cleanup script for Wizard Scan Bot.

Kya karta hai:
  - users.json load karta hai (jahan bhi DATA_DIR set hai — same jaisa bot mein)
  - Sirf un ~300 usernames ko rakhta hai jo KEEP_USERNAMES list mein hain
  - Baaki SAARE (fake/padded) entries permanently delete kar deta hai
  - Ek backup (users_backup_before_prune.json) bhi bana deta hai — safety ke liye

Is ke baad bot ka add_user() function bilkul pehle jaisa hi kaam karega —
jab bhi koi NAYA (real) member bot se interact karega, wo normally
users.json mein add ho jayega aur count badhta rahega. Is script mein
koi permanent change nahi hai add_user() ke logic mein — sirf ek baar
purana fake data saaf kar raha hai.

Run karne ka tareeqa (Railway pe):
  1. Railway project ke shell/console mein jao (ya "railway run" use karo)
  2. Is file ko apne bot ke folder mein rakho (jahan users.json hai)
  3. Agar DATA_DIR env var set hai to wo automatically use hoga
  4. Chalao: python prune_users.py
  5. Output check karo — kitne users kept, kitne deleted
  6. Agar sab sahi laga to bot restart kar do
"""

import json
import os

# ── Wahi DATA_DIR convention jo bot khud use karta hai ──────────────────────
DATA_DIR = os.environ.get("DATA_DIR", ".")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
BACKUP_FILE = os.path.join(DATA_DIR, "users_backup_before_prune.json")

# ── In 300 usernames ko rakhna hai (jo pehle 3 broadcast pages mein thay) ───
KEEP_USERNAMES = {
    "Thomass876", "Farhad_Loyal", "DiceParadiseAdminn", "BillyBassedd", "trenchkajun",
    "Wizard_Scan", "Redhulk_Gamble", "Whellplayed", "alexalex1141", "RAVENCALLSOWNER",
    "TeslaFoundr", "MaxRy1", "okifii", "Levisakerman", "TR1STAAN", "MdSaadman",
    "Globalm77", "SLayer_steel", "GambitThug", "callofzeus", "AlleyaAllena", "LemonYomo",
    "Mr_wick_007", "pikachu_isback", "Pappicalls_owner", "SquirreIMoon", "Nexxdegen",
    "Anas_a91", "Beni_XD", "marksgems1", "Stuknet_q", "jemsmoney", "chris_web1",
    "DannyLovesPizza", "Damnedonweb3", "eq8t254ii1i", "Almustapha1000", "Halpahalli69",
    "izzygithaiga9", "Web3Promotions", "Ulk3R", "Charlesj54", "ninjasoldyor", "wizmgweb3",
    "Jones_Parker1", "mustarseed001", "Moon_Whales1", "bigmezy1", "Khanpti00", "Web3Kate1",
    "Jessica51rr", "TrueChad27", "awokou7h30c", "ETHDisciple", "qqsswwxxqq", "aymipo",
    "PythonFounder", "Cmo_ZiChin9", "ZeLdaCrypto", "BasedJing", "realmoonshotbro",
    "godcandleprinter", "NouraKOLs", "Zayan_kols", "KillSwitch3ngagee", "CryptoWaveB",
    "MrDefiDoxx", "Based_ROYAL", "hellamem", "Power_Crypto72", "Galivin", "burhanxxxcc",
    "Bursaliibo", "miss_wow779", "mosad357", "MaverickJ6", "Shadow_Cooks", "Smarty6789",
    "bombirastaclat", "Mr_Fannn_C", "LOoyalll", "Runchu4", "Ayush211016", "llkhrfvb",
    "RamJOfficiaL", "Mr_Whales", "tammy8i", "Pappicall_owner", "Sunny8123", "adamorganista",
    "AnonBSC", "jacksonanthonyk9942", "TomCruiseOG", "Paul_Mkt6", "Dawlking5", "Andres5010",
    "jing_miee", "KOLZOWNER", "Cryptonite147", "PHILLCONTACT", "MichaelSystems",
    "Nailongcall", "x7i43089d1", "AntoTech07", "FETBOSS", "lightyagami28", "JOEINPOST",
    "Twhale666", "Abdilrahman2027", "Mr_whaleBackup", "Riveraboy", "TrenchMarkG",
    "Abverty588", "ste_vnn", "Mr_unique9t", "Tommatthew_B", "Shilllprofessor", "alereselll",
    "dudehash", "Wizyenore", "shahroze", "theredemperor", "rostik_555ccc", "mnain2k",
    "OneSpawn", "ZENITP2P", "YooLion", "Leventtao2783", "Shizuoka_BTC", "kyngxa1",
    "Shahin21ee", "AlladinXian", "iSilentV", "ALPHACALLSOWNER", "PabloOwn", "Gorrila1_CEO",
    "Ti_Reni", "SuperDegenCrypto", "Krlto", "MangoCryptos", "Yuhan_w", "work_hard13",
    "anmol900527", "Ceo_Finn", "Mohamad_zaregold", "crypt0ballsyjo", "beast1t", "ElZion0",
    "ArthurKolsManager", "moonboychad", "IrisKOLS", "Max_OG1", "wiseeth",
    "chadowencaIIsdev", "SINATRENDINGPVT", "PappiCallsOwner", "ccorange8", "Yeppi13",
    "VenomXOwner", "JohnVenomOwner", "Chaiquan_Ge", "wencto100xxx", "TheMiniDraft",
    "Hyakkimaru28", "taradwifik", "Dejiurrix", "Nataliacalls", "tuf_fG", "madMax18e",
    "ElenaCioran", "WhaleNinja_go", "kieIeth", "Clocklocker69", "JainMKT1", "deadpooldegen",
    "DirtyTraxx11", "Anna_PearL1", "ZhlKoglu", "ApexHuntz", "yqu9_2kz2c", "Ashley_Harris6764",
    "huiguang_67", "Modaji4_334", "Cl4yt0n_626", "ad4m_15", "itsSharon34", "v3linnnna4655",
    "elaiiiinnnnnne", "ca5ssss5andra22", "ymmmmm4nywaftkhr69", "Ruben_9385",
    "Tammy_lucas_440", "Konstantin9270", "amallllllmmmmmjd11", "allis0ooooon26",
    "alhyyyaaajml68", "mb3r90", "le3e1bi1073", "tWx6zWx6pWx6BWx6R", "constanceeeee584",
    "MichelePatterson4273", "chunfenghuayu7375", "Brendacalhoun85", "MOXIEQIANZOU_YAN",
    "St3ph3n_752", "y4rb4frjha_69", "iris2266", "AnnaBobylev", "Joshuakemp76438",
    "brrrryan79", "Gordon_3551", "theJwjw26", "Abwalqys2006", "vlaaadiiiiiislav_61",
    "sar4hhhh44", "Tlhh2000", "Michellewelch_374", "thePyotr55", "Abwhkym45", "xsYJb",
    "Biemiliangezhishichuanshuo24", "theBrian93", "Amyr493", "amychristensen17",
    "Marymelendez_989", "Rangwodonggemian6780", "eddddithhh45", "itsLarry93",
    "awtaral4hzan_74", "Heidi_Velazquez340", "luuuuuutttther", "rad0mmmmmir_407",
    "mrTlal83", "s4mera4aah38", "Jeffreychavez7547", "xianggggggua_23", "courrrtneeeey",
    "Kira_9681", "hsn4173", "itsLawrence85", "almardalassssmr21", "alikhtsarintsar_19",
    "ly455", "Hector5983", "heiiiiibao0oo1792", "katerin452", "t4mer20", "D4v1_754",
    "Lori_miranda2016", "CHUCIJIANMIAN_SHUAI", "nnnnnbuuuuuul67", "Emily_fernandez_450",
    "njw4n67", "olikjuyh8574", "Glenn_Daniels_64975", "theZhuyafengsheng26",
    "dg4r_gonzalez", "elizavetttta734", "muhhhhmd_51", "josefranco56", "konstanttttin710",
    "fern4nd0_95", "Aliraza3456783", "fdsfdsfdsfdsfdsfdsfddf", "thePatricia37", "La1l419",
    "yuhuaaan_72", "G0rd0n_217", "abwhsyn93", "Jekejehebdjd", "laur3n88", "D4wn52",
    "theMalcolm89", "l1na_13", "Jacqueline_2721", "mmmmmmmmmm48774", "addddddm885",
    "an54bwmajd34", "alyhsnnnn", "x4ng88lvkoxh", "lghfry78", "arkhip2005",
    "Heihuzhanshenwang", "P4t_918", "adel1na_79", "Ulya6631", "thaaaaerabdalrhmn",
    "n1col3_89", "Faith_castillo2929", "morttttteza_30", "jarrrree3eeed_102",
    "Bradley3740", "brrrrruceeee", "Mauricegrant_29", "timofey9254", "Anna_Gill_8405",
    "velimirrrrrr908", "Zhonghua2000", "laurennnn200", "nsss5ssymalrwh3745",
}
KEEP_USERNAMES_LOWER = {u.lower() for u in KEEP_USERNAMES}


def main():
    if not os.path.exists(USERS_FILE):
        print(f"❌ users.json nahi mila is path pe: {USERS_FILE}")
        return

    with open(USERS_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # Old list-format ko bhi handle karo (bot khud bhi ye karta hai)
    if isinstance(raw, list):
        users = {str(uid): {"id": uid, "username": None, "name": None} for uid in raw}
    else:
        users = raw

    total_before = len(users)

    # Backup pehle bana lo — kuch ghalat ho to wapas restore kar sako
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)
    print(f"🗄️  Backup ban gaya: {BACKUP_FILE} ({total_before} users)")

    kept = {}
    for uid, info in users.items():
        uname = (info.get("username") or "").lower()
        if uname and uname in KEEP_USERNAMES_LOWER:
            kept[uid] = info

    matched_usernames = {v.get("username", "").lower() for v in kept.values()}
    not_found = KEEP_USERNAMES_LOWER - matched_usernames

    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(kept, f, indent=2, ensure_ascii=False)

    print(f"✅ Done.")
    print(f"   Pehle total users: {total_before}")
    print(f"   Ab rakhe gaye:      {len(kept)}")
    print(f"   Delete kiye gaye:   {total_before - len(kept)}")
    if not_found:
        print(f"   ⚠️ {len(not_found)} username(s) keep-list mein thay lekin users.json mein nahi milay "
              f"(shayad wo kabhi bot se interact hi nahi hue): {sorted(not_found)}")


if __name__ == "__main__":
    main()
