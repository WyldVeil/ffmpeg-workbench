# Bundled fonts

Every font shipped in `assets/fonts` is free to redistribute:

| File | Family | Licence |
|------|--------|---------|
| Anton.ttf | Anton | SIL OFL 1.1 (`anton-OFL.txt`) |
| Bangers.ttf | Bangers | SIL OFL 1.1 (`bangers-OFL.txt`) |
| BebasNeue.ttf | Bebas Neue | SIL OFL 1.1 (`bebasneue-OFL.txt`) |
| Bungee.ttf | Bungee | SIL OFL 1.1 (`bungee-OFL.txt`) |
| Inter.ttf, Inter-Bold.ttf | Inter | SIL OFL 1.1 (`inter-OFL.txt`) |
| Lato.ttf, Lato-Bold.ttf | Lato | SIL OFL 1.1 (`lato-OFL.txt`) |
| LuckiestGuy.ttf | Luckiest Guy | Apache 2.0 (`luckiestguy-LICENSE.txt`) |
| Montserrat.ttf, Montserrat-Bold.ttf | Montserrat | SIL OFL 1.1 (`montserrat-OFL.txt`) |
| OpenSans.ttf, OpenSans-Bold.ttf | Open Sans | SIL OFL 1.1 (`opensans-OFL.txt`) |
| Poppins.ttf, Poppins-Bold.ttf | Poppins | SIL OFL 1.1 (`poppins-OFL.txt`) |
| Raleway.ttf, Raleway-Bold.ttf | Raleway | SIL OFL 1.1 (`raleway-OFL.txt`) |
| RobotoBlack.ttf | Roboto | SIL OFL 1.1 (`roboto-OFL.txt`) |

`Impact.ttf` (Monotype) and `TheBoldFont.ttf` (Sven Pels, "all rights
reserved") were removed in 2.0 because they can't be redistributed.
When a user picks "Impact", `core.filters.find_font` uses the system's
own Impact if one is installed (it ships with Windows) and otherwise falls
back to Anton.
