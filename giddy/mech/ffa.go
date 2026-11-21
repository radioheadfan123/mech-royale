package mech

// Ours (not in giddy's original): parameters for free-for-all play, applied once the bot decides the match is not
// practice (5 ticks after a death it did not cause). Practice play is unchanged.
func ffaParams(p Params) Params {
	p.RetreatHP, p.RecoverHP = 0.5, 0.9
	return p
}
