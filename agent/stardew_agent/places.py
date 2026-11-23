"""Where things are, by the names people use."""

POOL_WATER = [(15, 15), (10, 14), (18, 16), (12, 13)]  # inside the spa's pool; stepping in starts swimming
TILES_PER_MINUTE, MINUTES_PER_DOOR = 4.0, 2  # fitted to real walks: Mountain and Beach to the farmhouse both take 50 minutes

# what people call places, by the game's names
PLACES = {"pierre": "SeedShop", "pierres": "SeedShop", "generalstore": "SeedShop", "wizardtower": "WizardHouse", "wizard": "WizardHouse", "tower": "WizardHouse",
          "blacksmith": "Blacksmith", "clint": "Blacksmith", "guild": "AdventureGuild", "adventurersguild": "AdventureGuild", "joja": "JojaMart",
          "museum": "ArchaeologyHouse", "library": "ArchaeologyHouse", "carpenter": "ScienceHouse", "robin": "ScienceHouse", "carpentershop": "ScienceHouse",
          "marnie": "AnimalShop", "ranch": "AnimalShop", "willy": "FishShop", "fishshop": "FishShop", "clinic": "Hospital", "harvey": "Hospital",
          "communitycenter": "CommunityCenter", "cc": "CommunityCenter", "mines": "Mine", "home": "FarmHouse", "house": "FarmHouse", "farmhouse": "FarmHouse"}

# people who aren't on the friendship list, and where they're usually found
WORKPLACES = {"Marlon": "AdventureGuild", "Gil": "AdventureGuild", "Gunther": "ArchaeologyHouse", "Morris": "JojaMart", "Krobus": "Sewer", "Dwarf": "Mine",
              "Sandy": "SandyHouse", "Bouncer": "Club", "Mister Qi": "QiNutRoom", "Henchman": "WitchSwamp"}
