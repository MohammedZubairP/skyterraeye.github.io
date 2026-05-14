// ═══════════════════════════════════════════════════════
// SELF-CONTAINED SURVEY CANVAS ANIMATIONS
// Each function takes a canvas element and starts an animation
// ═══════════════════════════════════════════════════════

function animTotalStation(cv) {
  var cx = cv.getContext('2d'), t = 0;
  function rs() { cv.width = cv.offsetWidth || 800; cv.height = cv.offsetHeight || 380; }
  rs(); window.addEventListener('resize', rs);
  var pts = [];
  function draw() {
    var W = cv.width, H = cv.height;
    // Sky gradient
    var sky = cx.createLinearGradient(0, 0, 0, H * .55);
    sky.addColorStop(0, '#020A14'); sky.addColorStop(.5, '#061828'); sky.addColorStop(1, '#0A2235');
    cx.fillStyle = sky; cx.fillRect(0, 0, W, H);
    cx.fillStyle = '#070F18'; cx.fillRect(0, H * .52, W, H);
    // Stars
    if (!cv._st) cv._st = Array.from({length: 60}, function() { return {x: Math.random(), y: Math.random() * .45, r: Math.random() * 1.5 + .3, p: Math.random() * 6.28}; });
    cv._st.forEach(function(s) { var a = .3 + .3 * Math.sin(t * .02 + s.p); cx.fillStyle = 'rgba(200,230,255,' + a + ')'; cx.beginPath(); cx.arc(s.x * W, s.y * H, s.r, 0, 6.28); cx.fill(); });
    // Buildings
    [[.04,.52,.05,.3],[.10,.52,.04,.2],[.16,.52,.06,.24],[.23,.52,.05,.34],[.30,.52,.07,.16],[.38,.52,.06,.28],[.58,.52,.07,.24],[.66,.52,.08,.18],[.75,.52,.06,.30],[.83,.52,.07,.22],[.91,.52,.07,.14]].forEach(function(b) { cx.fillStyle = '#0D1E2C'; cx.fillRect(b[0]*W, b[1]*H-b[3]*H, b[2]*W, b[3]*H+2); });
    // Windows
    if (!cv._bw) { cv._bw = []; var blds = [[.04,.05],[.10,.04],[.16,.06],[.23,.05],[.30,.07],[.38,.06],[.58,.07],[.66,.08],[.75,.06],[.83,.07],[.91,.07]]; blds.forEach(function(b,i) { for (var r=0;r<8;r++) for (var c=0;c<3;c++) cv._bw.push({x:b[0]+.005+c*.012,bx:b[0],bw:b[1],r:r,c:c,on:Math.random()>.3,flip:Math.random()*200|0}); }); }
    cv._bw.forEach(function(w) { if (t%w.flip===0) w.on=!w.on; if(!w.on)return; cx.fillStyle='rgba(255,235,120,.6)'; cx.fillRect((w.bx*W+3+w.c*10), H*.52-20-w.r*16, 6, 8); });
    // Scanning beam
    var bx = .44*W, by = .64*H, ang = -.5 + Math.sin(t * .015) * .9, len = W * .38;
    cx.save(); cx.translate(bx, by); cx.rotate(ang);
    var beam = cx.createLinearGradient(0, 0, len, 0);
    beam.addColorStop(0, 'rgba(31,168,217,.5)'); beam.addColorStop(.7, 'rgba(31,168,217,.15)'); beam.addColorStop(1, 'rgba(31,168,217,0)');
    cx.fillStyle = beam; cx.beginPath(); cx.moveTo(0, 0); cx.lineTo(len, -15); cx.lineTo(len, 15); cx.closePath(); cx.fill(); cx.restore();
    // Scan points accumulating
    if (t % 3 === 0) { var px = bx + Math.cos(ang) * (len * (.5 + Math.random() * .5)); var py = by + Math.sin(ang) * (len * (.5 + Math.random() * .5)); pts.push({x:px,y:py,a:1}); if (pts.length > 80) pts.shift(); }
    pts.forEach(function(p) { p.a -= .005; if(p.a<0)p.a=0; cx.fillStyle='rgba(31,168,217,'+p.a+')'; cx.beginPath(); cx.arc(p.x,p.y,2,0,6.28); cx.fill(); });
    // Instrument
    cx.fillStyle='rgba(31,168,217,.9)'; cx.beginPath(); cx.arc(bx,by,7,0,6.28); cx.fill();
    cx.fillStyle='rgba(31,168,217,.3)'; cx.beginPath(); cx.arc(bx,by,14,0,6.28); cx.fill();
    // Grid overlay
    cx.strokeStyle='rgba(31,168,217,.04)'; cx.lineWidth=1;
    for(var gx=0;gx<W;gx+=60){cx.beginPath();cx.moveTo(gx,0);cx.lineTo(gx,H);cx.stroke();}
    for(var gy=0;gy<H;gy+=60){cx.beginPath();cx.moveTo(0,gy);cx.lineTo(W,gy);cx.stroke();}
    t++; requestAnimationFrame(draw);
  }
  draw();
}

function animGNSS(cv) {
  var cx = cv.getContext('2d'), t = 0;
  function rs() { cv.width = cv.offsetWidth || 800; cv.height = cv.offsetHeight || 380; }
  rs(); window.addEventListener('resize', rs);
  var sats = Array.from({length:8}, function(_, i) { return {a: i/8*6.28, r:.38, spd:.008+Math.random()*.005, color:['#60A5FA','#34D399','#F59E0B','#A78BFA','#60A5FA','#34D399','#F59E0B','#A78BFA'][i]}; });
  var signals = [];
  function draw() {
    var W = cv.width, H = cv.height, cx_ = W/2, cy_ = H/2;
    cx.fillStyle = '#030A18'; cx.fillRect(0,0,W,H);
    // Stars
    if (!cv._st2) cv._st2 = Array.from({length:80},function(){return{x:Math.random()*W,y:Math.random()*H*.6,r:Math.random()*1+.2,p:Math.random()*6.28};});
    cv._st2.forEach(function(s){var a=.2+.25*Math.sin(t*.015+s.p);cx.fillStyle='rgba(255,255,255,'+a+')';cx.beginPath();cx.arc(s.x,s.y,s.r,0,6.28);cx.fill();});
    // Earth
    var eg=cx.createRadialGradient(cx_,cy_,0,cx_,cy_,W*.2);
    eg.addColorStop(0,'#1E3A5F');eg.addColorStop(.6,'#0A2540');eg.addColorStop(1,'#061828');
    cx.fillStyle=eg;cx.beginPath();cx.arc(cx_,cy_,W*.2,0,6.28);cx.fill();
    cx.strokeStyle='rgba(47,126,255,.3)';cx.lineWidth=1.5;cx.beginPath();cx.arc(cx_,cy_,W*.2,0,6.28);cx.stroke();
    // Orbit rings
    [.38,.46,.55].forEach(function(r,i){cx.strokeStyle='rgba(47,126,255,.07)';cx.lineWidth=1;cx.beginPath();cx.arc(cx_,cy_,W*r,0,6.28);cx.stroke();});
    // Satellites
    sats.forEach(function(s,i){
      s.a+=s.spd;
      var r=W*(s.r+i*.025),sx=cx_+Math.cos(s.a)*r,sy=cy_+Math.sin(s.a)*r*.7;
      cx.fillStyle=s.color;cx.beginPath();cx.arc(sx,sy,5,0,6.28);cx.fill();
      cx.strokeStyle=s.color;cx.lineWidth=1.5;cx.strokeRect(sx-8,sy-3,16,6);
      // Signal beam to earth
      if(t%20===i*2){signals.push({sx:sx,sy:sy,prog:0,col:s.color});}
    });
    signals.forEach(function(sg,i){
      sg.prog+=.04;
      var px=sg.sx+(cx_-sg.sx)*sg.prog,py=sg.sy+(cy_-sg.sy)*sg.prog;
      cx.strokeStyle=sg.col.replace(')',','+(1-sg.prog)+')').replace('rgb','rgba');
      cx.lineWidth=1.5;cx.beginPath();cx.moveTo(sg.sx,sg.sy);cx.lineTo(px,py);cx.stroke();
      cx.fillStyle=sg.col;cx.beginPath();cx.arc(px,py,3,0,6.28);cx.fill();
      if(sg.prog>=1)signals.splice(i,1);
    });
    // Accuracy circle pulsing
    var ac=(t%60)/60;cx.strokeStyle='rgba(52,211,153,'+(1-ac)+')';cx.lineWidth=2;cx.beginPath();cx.arc(cx_,cy_,W*.2*(1+ac*.3),0,6.28);cx.stroke();
    // Coord text
    cx.fillStyle='rgba(52,211,153,.8)';cx.font='bold '+(W*.018)+'px IBM Plex Mono,monospace';
    cx.fillText('N '+((25.2+Math.sin(t*.003)*.0001).toFixed(6))+'°',cx_-W*.12,cy_+W*.25);
    cx.fillText('E '+((55.27+Math.cos(t*.002)*.0001).toFixed(6))+'°',cx_-W*.12,cy_+W*.28);
    cx.fillText('±'+((8+Math.sin(t*.1)*3).toFixed(0))+'mm',cx_+W*.05,cy_+W*.28);
    t++;requestAnimationFrame(draw);
  }
  draw();
}

function animSetting(cv) {
  var cx_ = cv.getContext('2d'), t = 0;
  function rs() { cv.width = cv.offsetWidth || 800; cv.height = cv.offsetHeight || 380; }
  rs(); window.addEventListener('resize', rs);
  var revealed = 0;
  var gridPts = [];
  for(var gr=0;gr<5;gr++) for(var gc=0;gc<7;gc++) gridPts.push({r:gr,c:gc,rev:gr*7+gc});
  function draw() {
    var W = cv.width, H = cv.height;
    cx_.fillStyle = '#06101E'; cx_.fillRect(0,0,W,H);
    // Grid background
    cx_.strokeStyle='rgba(47,126,255,.06)';cx_.lineWidth=1;
    for(var x=0;x<W;x+=50){cx_.beginPath();cx_.moveTo(x,0);cx_.lineTo(x,H);cx_.stroke();}
    for(var y=0;y<H;y+=50){cx_.beginPath();cx_.moveTo(0,y);cx_.lineTo(W,y);cx_.stroke();}
    var gsx=W*.1, gsy=H*.15, gew=W*.8, geh=H*.7;
    var cols=7,rows=5,cw=gew/(cols-1),ch=geh/(rows-1);
    // Construction site ground
    cx_.fillStyle='rgba(20,40,20,.4)';cx_.fillRect(gsx-20,gsy+geh-10,gew+40,H);
    // Grid lines
    var rev = Math.floor(t/8) % (cols*rows);
    gridPts.forEach(function(p) {
      if(p.rev>rev) return;
      var px=gsx+p.c*cw, py=gsy+p.r*ch;
      // Column footings
      cx_.fillStyle='rgba(255,200,50,.15)';cx_.strokeStyle='rgba(255,200,50,.4)';cx_.lineWidth=1.5;
      cx_.beginPath();cx_.rect(px-12,py-12,24,24);cx_.fill();cx_.stroke();
      // Nails
      cx_.fillStyle='#F59E0B';cx_.beginPath();cx_.arc(px,py,4,0,6.28);cx_.fill();
      // Labels
      cx_.fillStyle='rgba(255,200,50,.8)';cx_.font='bold '+(W*.013)+'px IBM Plex Mono,monospace';
      cx_.fillText(String.fromCharCode(65+p.c)+(p.r+1),px+6,py-6);
    });
    // Grid lines connecting
    for(var r2=0;r2<rows;r2++){for(var c2=0;c2<cols-1;c2++){var idx=r2*cols+c2;if(idx>rev)continue;cx_.strokeStyle='rgba(255,200,50,.2)';cx_.lineWidth=1;cx_.beginPath();cx_.moveTo(gsx+c2*cw,gsy+r2*ch);cx_.lineTo(gsx+(c2+1)*cw,gsy+r2*ch);cx_.stroke();}}
    for(var r3=0;r3<rows-1;r3++){for(var c3=0;c3<cols;c3++){var idx2=r3*cols+c3;if(idx2>rev)continue;cx_.strokeStyle='rgba(255,200,50,.2)';cx_.lineWidth=1;cx_.beginPath();cx_.moveTo(gsx+c3*cw,gsy+r3*ch);cx_.lineTo(gsx+c3*cw,gsy+(r3+1)*ch);cx_.stroke();}}
    // Instrument at bottom
    var ix=W*.5, iy=H*.92;
    cx_.fillStyle='rgba(47,126,255,.8)';cx_.beginPath();cx_.arc(ix,iy,8,0,6.28);cx_.fill();
    // Laser to current point
    if(rev<gridPts.length){var cp=gridPts[rev];var tx=gsx+cp.c*cw,ty=gsy+cp.r*ch;cx_.strokeStyle='rgba(47,126,255,.6)';cx_.lineWidth=1.5;cx_.setLineDash([4,4]);cx_.beginPath();cx_.moveTo(ix,iy);cx_.lineTo(tx,ty);cx_.stroke();cx_.setLineDash([]);}
    // Counter
    cx_.fillStyle='rgba(47,126,255,.8)';cx_.font='bold '+(W*.015)+'px IBM Plex Mono,monospace';
    cx_.fillText('POINTS SET: '+Math.min(rev+1,cols*rows)+'/'+cols*rows,W*.04,H*.95);
    t++;requestAnimationFrame(draw);
  }
  draw();
}

function animLevel(cv) {
  var cx_ = cv.getContext('2d'), t = 0;
  function rs() { cv.width = cv.offsetWidth || 800; cv.height = cv.offsetHeight || 380; }
  rs(); window.addEventListener('resize', rs);
  var ground = [], readings = [];
  for(var i=0;i<20;i++) ground.push({x:i/19,y:.55+Math.random()*.15,rl:(5+Math.random()*.8).toFixed(3)});
  function draw() {
    var W = cv.width, H = cv.height;
    cx_.fillStyle='#06101E';cx_.fillRect(0,0,W,H);
    // Grid
    cx_.strokeStyle='rgba(47,126,255,.05)';cx_.lineWidth=1;
    for(var x=0;x<W;x+=50){cx_.beginPath();cx_.moveTo(x,0);cx_.lineTo(x,H);cx_.stroke();}
    for(var y=0;y<H;y+=50){cx_.beginPath();cx_.moveTo(0,y);cx_.lineTo(W,y);cx_.stroke();}
    // Ground profile
    cx_.strokeStyle='rgba(52,211,153,.5)';cx_.lineWidth=2;cx_.beginPath();
    ground.forEach(function(p,i){var px=p.x*W,py=p.y*H;if(i===0)cx_.moveTo(px,py);else cx_.lineTo(px,py);});
    cx_.stroke();
    // Fill ground
    cx_.fillStyle='rgba(20,60,30,.3)';cx_.beginPath();
    ground.forEach(function(p,i){var px=p.x*W,py=p.y*H;if(i===0)cx_.moveTo(px,py);else cx_.lineTo(px,py);});
    cx_.lineTo(W,H);cx_.lineTo(0,H);cx_.closePath();cx_.fill();
    // Datum line
    cx_.strokeStyle='rgba(239,68,68,.4)';cx_.lineWidth=1;cx_.setLineDash([8,4]);
    cx_.beginPath();cx_.moveTo(0,H*.6);cx_.lineTo(W,H*.6);cx_.stroke();cx_.setLineDash([]);
    cx_.fillStyle='rgba(239,68,68,.8)';cx_.font=(W*.013)+'px IBM Plex Mono,monospace';
    cx_.fillText('DATUM RL +5.000m',W*.02,H*.58);
    // Level instrument travelling
    var instrPos = (t%200)/200;
    var gi=Math.floor(instrPos*19),gp=ground[gi]||ground[0];
    var ix=instrPos*W, iy=gp.y*H-20;
    // Staff at some distance
    var staffPos = (instrPos+.2)%1, si2=Math.floor(staffPos*19),sp=ground[si2]||ground[0];
    var sx2=staffPos*W, sy2=sp.y*H;
    // Staff rod
    cx_.fillStyle='rgba(255,255,255,.9)';cx_.fillRect(sx2-2,sy2-60,4,60);
    cx_.fillStyle='rgba(239,68,68,.8)';for(var m=0;m<6;m++){cx_.fillRect(sx2-2,sy2-m*10-10,4,5);}
    // Reading beam
    var bsight=sy2-H*.6+H*.005*Math.sin(t*.05);
    cx_.strokeStyle='rgba(52,211,153,.7)';cx_.lineWidth=1.5;
    cx_.beginPath();cx_.moveTo(ix,iy);cx_.lineTo(sx2,iy);cx_.stroke();
    // Reading display
    var reading=(H*.6-sy2)/H*2+3;
    cx_.fillStyle='rgba(52,211,153,.9)';cx_.font='bold '+(W*.015)+'px IBM Plex Mono,monospace';
    cx_.fillText('BS: '+(Math.abs(reading)+1.2).toFixed(3)+'m',sx2-20,sy2-70);
    // Instrument
    cx_.fillStyle='#3B82F6';cx_.beginPath();cx_.arc(ix,iy,7,0,6.28);cx_.fill();
    cx_.fillStyle='rgba(59,130,246,.3)';cx_.beginPath();cx_.arc(ix,iy,14,0,6.28);cx_.fill();
    // RL display
    cx_.fillStyle='rgba(255,200,50,.9)';cx_.font='bold '+(W*.016)+'px IBM Plex Mono,monospace';
    cx_.fillText('RL: +'+(5+Math.abs(bsight/100)).toFixed(3)+'m EGM2008',W*.04,H*.12);
    t++;requestAnimationFrame(draw);
  }
  draw();
}

function animPointCloud(cv) {
  var cx_ = cv.getContext('2d'), t = 0;
  function rs() { cv.width = cv.offsetWidth || 800; cv.height = cv.offsetHeight || 380; }
  rs(); window.addEventListener('resize', rs);
  var pts3d = [], maxPts = 2000;
  // Building outline
  var outline = [{x:.25,y:.7},{x:.25,y:.2},{x:.45,y:.1},{x:.65,y:.2},{x:.75,y:.2},{x:.75,y:.7}];
  function addPts() {
    for(var i=0;i<15;i++){
      var side=Math.random();
      var pt;
      if(side<.2){pt={x:.25+Math.random()*.01,y:.2+Math.random()*.5,z:Math.random(),col:'#60A5FA'};}
      else if(side<.4){pt={x:.75+Math.random()*.01-Math.random()*.01,y:.2+Math.random()*.5,z:Math.random(),col:'#60A5FA'};}
      else if(side<.6){pt={x:.25+Math.random()*.5,y:.7+Math.random()*.01,z:Math.random(),col:'#34D399'};}
      else if(side<.8){pt={x:.25+Math.random()*.5,y:.2+Math.random()*.01,z:Math.random(),col:'#F59E0B'};}
      else{pt={x:.25+Math.random()*.5,y:.15+Math.random()*.01,z:Math.random()*.05,col:'#A78BFA'};}
      pts3d.push(pt);
      if(pts3d.length>maxPts)pts3d.shift();
    }
  }
  function draw() {
    var W=cv.width,H=cv.height;
    cx_.fillStyle='#030A18';cx_.fillRect(0,0,W,H);
    cx_.strokeStyle='rgba(47,126,255,.04)';cx_.lineWidth=1;
    for(var x=0;x<W;x+=50){cx_.beginPath();cx_.moveTo(x,0);cx_.lineTo(x,H);cx_.stroke();}
    for(var y=0;y<H;y+=50){cx_.beginPath();cx_.moveTo(0,y);cx_.lineTo(W,y);cx_.stroke();}
    addPts();
    pts3d.forEach(function(p){
      var px=p.x*W,py=p.y*H+p.z*30;
      var col=p.col||'#60A5FA';
      var a=.4+p.z*.6;
      cx_.fillStyle=col.replace(')',','+a+')').replace('rgb','rgba').replace('#60A5FA','rgba(96,165,250,'+a+')').replace('#34D399','rgba(52,211,153,'+a+')').replace('#F59E0B','rgba(245,158,11,'+a+')').replace('#A78BFA','rgba(167,139,250,'+a+')');
      cx_.beginPath();cx_.arc(px,py,1.5,0,6.28);cx_.fill();
    });
    // Scan lines sweeping
    var scanY=(t%H);
    cx_.strokeStyle='rgba(96,165,250,.08)';cx_.lineWidth=2;
    cx_.beginPath();cx_.moveTo(0,scanY);cx_.lineTo(W,scanY);cx_.stroke();
    // Point count
    cx_.fillStyle='rgba(96,165,250,.9)';cx_.font='bold '+(W*.015)+'px IBM Plex Mono,monospace';
    cx_.fillText('POINTS: '+pts3d.length.toLocaleString(),W*.04,H*.1);
    cx_.fillText('DENSITY: '+(pts3d.length/3|0)+'/m²',W*.04,H*.16);
    t++;requestAnimationFrame(draw);
  }
  draw();
}

function animGPR(cv) {
  var cx_ = cv.getContext('2d'), t = 0;
  function rs() { cv.width = cv.offsetWidth || 800; cv.height = cv.offsetHeight || 380; }
  rs(); window.addEventListener('resize', rs);
  var pipes = [{x:.3,y:.45,r:.03,col:'#EF4444',lbl:'WATER'},{x:.5,y:.55,r:.025,col:'#F59E0B',lbl:'GAS'},{x:.68,y:.42,r:.02,col:'#3B82F6',lbl:'ELEC'},{x:.2,y:.6,r:.035,col:'#8B5CF6',lbl:'TELE'},{x:.75,y:.58,r:.02,col:'#10B981',lbl:'DRAIN'}];
  function draw() {
    var W=cv.width,H=cv.height;
    // Surface
    cx_.fillStyle='#1A1A0A';cx_.fillRect(0,0,W,H*.38);
    // Road markings
    cx_.strokeStyle='rgba(255,255,255,.15)';cx_.lineWidth=2;cx_.setLineDash([20,15]);
    cx_.beginPath();cx_.moveTo(0,H*.19);cx_.lineTo(W,H*.19);cx_.stroke();cx_.setLineDash([]);
    // Ground layers
    var layers=[['#2D1F0E','rgba(120,80,40,.9)',.38,.52],['#1F150A','rgba(80,55,30,.8)',.52,.68],['#150E06','rgba(50,35,20,.7)',.68,.85],['#0D0A04','rgba(30,20,10,.6)',.85,1]];
    layers.forEach(function(l){cx_.fillStyle=l[1];cx_.fillRect(0,H*l[2],W,H*(l[3]-l[2]));});
    // Pipes
    pipes.forEach(function(p){
      var px=p.x*W,py=p.y*H,r=p.r*W;
      cx_.strokeStyle=p.col;cx_.lineWidth=3;cx_.beginPath();cx_.arc(px,py,r,0,6.28);cx_.stroke();
      cx_.fillStyle=p.col.replace(')',',0.1)').replace('rgb','rgba');cx_.beginPath();cx_.arc(px,py,r,0,6.28);cx_.fill();
      cx_.fillStyle=p.col;cx_.font='bold '+(W*.013)+'px IBM Plex Mono,monospace';cx_.fillText(p.lbl,px-r,py-r-5);
    });
    // GPR scanner moving across surface
    var scanX=(t%100)/100*W;
    cx_.fillStyle='rgba(255,200,50,.9)';cx_.fillRect(scanX-15,H*.3,30,14);
    cx_.fillStyle='rgba(255,200,50,.3)';cx_.fillRect(scanX-15,H*.3,30,14);
    // GPR waves going down
    for(var w=1;w<=5;w++){
      var wAge=(t%20)/20;var wR=W*.04*(w+wAge*.5);var wA=.5-w*.08;
      if(wA<=0)continue;
      cx_.strokeStyle='rgba(255,200,50,'+wA+')';cx_.lineWidth=1.5;
      cx_.beginPath();cx_.arc(scanX,H*.37,wR,0,Math.PI);cx_.stroke();
    }
    // Detection indicators on surface
    pipes.forEach(function(p){
      var dist=Math.abs(scanX-p.x*W);
      if(dist<W*.15){
        var strength=1-dist/(W*.15);
        cx_.fillStyle='rgba(239,68,68,'+(.3*strength)+')';
        cx_.fillRect(p.x*W-10,H*.28,20,8);
        cx_.strokeStyle=p.col;cx_.lineWidth=2;cx_.strokeRect(p.x*W-12,H*.27,24,10);
      }
    });
    // Scan line counter
    cx_.fillStyle='rgba(255,200,50,.9)';cx_.font='bold '+(W*.013)+'px IBM Plex Mono,monospace';
    cx_.fillText('GPR SCAN: '+(t%100)+'%',W*.04,H*.12);
    cx_.fillText('DEPTH: '+(1.5+Math.sin(t*.02)*.3).toFixed(1)+'m',W*.04,H*.18);
    t++;requestAnimationFrame(draw);
  }
  draw();
}

function animTraffic(cv) {
  var cx_ = cv.getContext('2d'), t = 0;
  function rs() { cv.width = cv.offsetWidth || 800; cv.height = cv.offsetHeight || 380; }
  rs(); window.addEventListener('resize', rs);
  var cars = [];
  var counts = {N:0,S:0,E:0,W:0};
  for(var i=0;i<12;i++) cars.push({dir:['N','S','E','W'][i%4],pos:Math.random(),spd:.003+Math.random()*.002,col:['#3B82F6','#EF4444','#F59E0B','#10B981','#8B5CF6'][i%5]});
  function draw() {
    var W=cv.width,H=cv.height,cx2=W/2,cy2=H/2;
    cx_.fillStyle='#111827';cx_.fillRect(0,0,W,H);
    // Roads
    cx_.fillStyle='#1F2937';cx_.fillRect(cx2-30,0,60,H);cx_.fillRect(0,cy2-30,W,60);
    cx_.strokeStyle='rgba(255,255,255,.3)';cx_.lineWidth=2;cx_.setLineDash([15,15]);
    cx_.beginPath();cx_.moveTo(cx2,0);cx_.lineTo(cx2,cy2-35);cx_.stroke();
    cx_.beginPath();cx_.moveTo(cx2,cy2+35);cx_.lineTo(cx2,H);cx_.stroke();
    cx_.beginPath();cx_.moveTo(0,cy2);cx_.lineTo(cx2-35,cy2);cx_.stroke();
    cx_.beginPath();cx_.moveTo(cx2+35,cy2);cx_.lineTo(W,cy2);cx_.stroke();
    cx_.setLineDash([]);
    // Junction box
    cx_.fillStyle='#374151';cx_.fillRect(cx2-35,cy2-35,70,70);
    // Move cars
    cars.forEach(function(c){
      c.pos+=c.spd;
      var cx3,cy3;
      if(c.dir==='N'){cx3=cx2-10;cy3=H-c.pos*H;if(cy3<cy2-35){c.pos=0;counts.N++;}}
      else if(c.dir==='S'){cx3=cx2+10;cy3=c.pos*H-H/2;if(cy3>H){c.pos=0;counts.S++;}}
      else if(c.dir==='E'){cx3=c.pos*W-W/2;cy3=cy2-10;if(cx3>W){c.pos=0;counts.E++;}}
      else{cx3=W-c.pos*W;cy3=cy2+10;if(cx3<cx2+35){c.pos=0;counts.W++;}}
      cx_.fillStyle=c.col;cx_.beginPath();cx_.arc(cx3||cx2,cy3||cy2,6,0,6.28);cx_.fill();
    });
    // Counters
    var total=counts.N+counts.S+counts.E+counts.W;
    cx_.fillStyle='rgba(0,0,0,.7)';cx_.fillRect(10,10,160,90);cx_.strokeStyle='rgba(59,130,246,.5)';cx_.strokeRect(10,10,160,90);
    cx_.fillStyle='#60A5FA';cx_.font='bold '+(W*.013)+'px IBM Plex Mono,monospace';
    cx_.fillText('TRAFFIC COUNT',18,28);
    cx_.fillStyle='#fff';
    cx_.fillText('N-bound: '+counts.N,18,44);cx_.fillText('S-bound: '+counts.S,18,56);
    cx_.fillText('E-bound: '+counts.E,18,68);cx_.fillText('W-bound: '+counts.W,18,80);
    cx_.fillStyle='#F59E0B';cx_.fillText('TOTAL: '+total,18,96);
    t++;requestAnimationFrame(draw);
  }
  draw();
}

// Auto-initialize any canvas with data-anim attribute
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('canvas[data-anim]').forEach(function(cv) {
    var type = cv.getAttribute('data-anim');
    if(type==='totalstation') animTotalStation(cv);
    else if(type==='gnss') animGNSS(cv);
    else if(type==='setting') animSetting(cv);
    else if(type==='level') animLevel(cv);
    else if(type==='pointcloud') animPointCloud(cv);
    else if(type==='gpr') animGPR(cv);
    else if(type==='traffic') animTraffic(cv);
  });
});
