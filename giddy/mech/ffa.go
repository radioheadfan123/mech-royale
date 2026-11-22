package mech

import (
	"fmt"
	"math"
	"os"
)

// Ours (not in giddy's original): parameters for free-for-all play, applied once the bot decides the match is not
// practice (5 ticks after a death it did not cause). Practice play is unchanged.
func ffaParams(p Params) Params {
	p.Crown = 0.8
	return p
}

// Ours: flip to free-for-all as soon as two other mechs are seen fighting each other (practice house bots only shoot at us).
var earlyFFA = false

// othersFighting: an enemy gun bullet (about 252 m/s; drone bullets are 90) heading at a seen mech more than 20 m from us,
// passing within 3 m of it in the next 80 m, while its line (both ways) stays 15 m or more from us, which rules out our own shots.
func (b *Brain) othersFighting(o *Obs) bool {
	me := o.Self.Pos
	for _, p := range o.Projectiles {
		sp := math.Sqrt(p.Vel[0]*p.Vel[0] + p.Vel[1]*p.Vel[1] + p.Vel[2]*p.Vel[2])
		if sp < 200 {
			continue
		}
		vx, vy := p.Vel[0]/sp, p.Vel[1]/sp
		h := math.Hypot(vx, vy)
		if h < 0.5 {
			continue
		}
		vx, vy = vx/h, vy/h
		miss := func(x, y float64) (float64, float64) {
			rx, ry := x-p.Pos[0], y-p.Pos[1]
			a := rx*vx + ry*vy
			return a, math.Abs(rx*vy - ry*vx)
		}
		if _, d := miss(me[0], me[1]); d < 15 { // its line runs through us: ours, or aimed at us
			continue
		}
		for _, t := range b.tracks {
			if t.Seen != o.T || hyp(t.Pos[0]-me[0], t.Pos[1]-me[1]) < 20 {
				continue
			}
			if a, d := miss(t.Pos[0], t.Pos[1]); a > 3 && a < 80 && d < 3 {
				if os.Getenv("MRDBG") != "" {
					fmt.Fprintf(os.Stderr, "earlyffa %.1f\n", o.T)
				}
				return true
			}
		}
	}
	return false
}
