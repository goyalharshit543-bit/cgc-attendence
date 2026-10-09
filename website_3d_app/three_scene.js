/**
 * FaceAttend 3D Engine & Holographic Face Visualizer
 * Powered by Three.js (WebGL)
 */

let heroScene, heroCamera, heroRenderer, heroControls;
let scanLaser, gimbalRing1, gimbalRing2, faceGroup, particleSystem;
let isLaserGoingDown = true;
let laserY = 1.8;

// Background spatial starfield
let bgScene, bgCamera, bgRenderer, bgStars;

export function init3DExperience() {
  if (typeof THREE === 'undefined') {
    console.warn('Three.js library is not loaded (offline or CDN blocked). Skipping 3D visual effects gracefully.');
    return;
  }
  initHeroViewport();
  initBackgroundStarfield();
  window.addEventListener('resize', onWindowResize);
}

/**
 * Initializes the main interactive 3D Hologram scanner in the hero card.
 */
function initHeroViewport() {
  if (typeof THREE === 'undefined') return;
  const container = document.getElementById('three-hero-viewport');
  if (!container) return;

  const width = container.clientWidth || 500;
  const height = container.clientHeight || 500;

  // Scene
  heroScene = new THREE.Scene();
  heroScene.fog = new THREE.FogExp2(0x05060f, 0.08);

  // Camera
  heroCamera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
  heroCamera.position.set(0, 0, 7.5);

  // Renderer
  heroRenderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  heroRenderer.setSize(width, height);
  heroRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  heroRenderer.toneMapping = THREE.ACESFilmicToneMapping;
  container.innerHTML = '';
  container.appendChild(heroRenderer.domElement);

  // Orbit Controls
  if (typeof THREE.OrbitControls !== 'undefined') {
    heroControls = new THREE.OrbitControls(heroCamera, heroRenderer.domElement);
    heroControls.enableDamping = true;
    heroControls.dampingFactor = 0.05;
    heroControls.enableZoom = true;
    heroControls.minDistance = 3.5;
    heroControls.maxDistance = 12;
    heroControls.autoRotate = true;
    heroControls.autoRotateSpeed = 1.0;
  }

  // Lighting
  const ambientLight = new THREE.AmbientLight(0x0a1a3a, 2.5);
  heroScene.add(ambientLight);

  const cyanKeyLight = new THREE.DirectionalLight(0x00e5ff, 2.0);
  cyanKeyLight.position.set(5, 5, 5);
  heroScene.add(cyanKeyLight);

  const violetRimLight = new THREE.DirectionalLight(0x8b5cf6, 2.2);
  violetRimLight.position.set(-5, -3, -4);
  heroScene.add(violetRimLight);

  const pointLaserLight = new THREE.PointLight(0x00e5ff, 3.0, 4);
  pointLaserLight.position.set(0, 0, 1.2);
  heroScene.add(pointLaserLight);

  // Build Procedural 3D Holographic Face & Reticle
  faceGroup = new THREE.Group();
  heroScene.add(faceGroup);

  // 1. Procedural Cybernetic Head Mesh (Icosahedron + wireframe compound)
  const headGeo = new THREE.IcosahedronGeometry(1.6, 3);
  const headMat = new THREE.MeshStandardMaterial({
    color: 0x051a2e,
    metalness: 0.8,
    roughness: 0.3,
    wireframe: true,
    emissive: 0x00e5ff,
    emissiveIntensity: 0.25,
  });
  const headMesh = new THREE.Mesh(headGeo, headMat);
  faceGroup.add(headMesh);

  // Inner solid glow core
  const coreGeo = new THREE.IcosahedronGeometry(1.4, 2);
  const coreMat = new THREE.MeshBasicMaterial({
    color: 0x0a2b4a,
    wireframe: false,
    transparent: true,
    opacity: 0.6,
  });
  const coreMesh = new THREE.Mesh(coreGeo, coreMat);
  faceGroup.add(coreMesh);

  // Facial feature landmarks / glowing neural nodes
  const dotsGeo = new THREE.BufferGeometry();
  const dotsCount = 180;
  const dotPositions = new Float32Array(dotsCount * 3);
  for (let i = 0; i < dotsCount * 3; i += 3) {
    const u = Math.random();
    const v = Math.random();
    const theta = u * 2.0 * Math.PI;
    const phi = Math.acos(2.0 * v - 1.0);
    const r = 1.62;
    dotPositions[i] = r * Math.sin(phi) * Math.cos(theta);
    dotPositions[i + 1] = r * Math.sin(phi) * Math.sin(theta);
    dotPositions[i + 2] = r * Math.cos(phi);
  }
  dotsGeo.setAttribute('position', new THREE.BufferAttribute(dotPositions, 3));
  const dotsMat = new THREE.PointsMaterial({
    size: 0.06,
    color: 0x00e5ff,
    transparent: true,
    opacity: 0.9,
  });
  const landmarks = new THREE.Points(dotsGeo, dotsMat);
  faceGroup.add(landmarks);

  // 2. Bounding 3D Vision Target Box (Reticle)
  const boxGeo = new THREE.BoxGeometry(3.6, 3.8, 3.6);
  const edges = new THREE.EdgesGeometry(boxGeo);
  const boxLineMat = new THREE.LineBasicMaterial({
    color: 0x8b5cf6,
    transparent: true,
    opacity: 0.35,
  });
  const boundingWire = new THREE.LineSegments(edges, boxLineMat);
  faceGroup.add(boundingWire);

  // 3. Scanning Laser Grid Plane
  const laserGeo = new THREE.RingGeometry(0.1, 2.3, 32);
  const laserMat = new THREE.MeshBasicMaterial({
    color: 0x00e5ff,
    side: THREE.DoubleSide,
    transparent: true,
    opacity: 0.65,
  });
  scanLaser = new THREE.Mesh(laserGeo, laserMat);
  scanLaser.rotation.x = Math.PI / 2;
  scanLaser.position.y = 0;
  faceGroup.add(scanLaser);

  // 4. Concentric Tech Gimbal Rings
  const ringGeo1 = new THREE.TorusGeometry(2.6, 0.02, 16, 100);
  const ringMat1 = new THREE.MeshBasicMaterial({
    color: 0x00e5ff,
    transparent: true,
    opacity: 0.6,
  });
  gimbalRing1 = new THREE.Mesh(ringGeo1, ringMat1);
  heroScene.add(gimbalRing1);

  const ringGeo2 = new THREE.TorusGeometry(3.1, 0.015, 16, 100);
  const ringMat2 = new THREE.MeshBasicMaterial({
    color: 0xec4899,
    transparent: true,
    opacity: 0.4,
  });
  gimbalRing2 = new THREE.Mesh(ringGeo2, ringMat2);
  heroScene.add(gimbalRing2);

  // 5. Surrounding Cyber Particle Swarm
  const particleCount = 200;
  const pGeo = new THREE.BufferGeometry();
  const pPositions = new Float32Array(particleCount * 3);
  for (let i = 0; i < particleCount * 3; i += 3) {
    pPositions[i] = (Math.random() - 0.5) * 12;
    pPositions[i + 1] = (Math.random() - 0.5) * 12;
    pPositions[i + 2] = (Math.random() - 0.5) * 12;
  }
  pGeo.setAttribute('position', new THREE.BufferAttribute(pPositions, 3));
  const pMat = new THREE.PointsMaterial({
    size: 0.04,
    color: 0x8b5cf6,
    transparent: true,
    opacity: 0.5,
  });
  particleSystem = new THREE.Points(pGeo, pMat);
  heroScene.add(particleSystem);

  // Start Animation Loop
  animateHero();
}

/**
 * Background ambient 3D starfield.
 */
function initBackgroundStarfield() {
  if (typeof THREE === 'undefined') return;
  const container = document.getElementById('bg-canvas-container');
  if (!container) return;

  bgScene = new THREE.Scene();
  bgCamera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 1, 1000);
  bgCamera.position.z = 400;

  bgRenderer = new THREE.WebGLRenderer({ alpha: true });
  bgRenderer.setSize(window.innerWidth, window.innerHeight);
  bgRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  container.appendChild(bgRenderer.domElement);

  const count = 750;
  const starGeo = new THREE.BufferGeometry();
  const starPos = new Float32Array(count * 3);
  for (let i = 0; i < count * 3; i += 3) {
    starPos[i] = (Math.random() - 0.5) * 1200;
    starPos[i + 1] = (Math.random() - 0.5) * 1200;
    starPos[i + 2] = (Math.random() - 0.5) * 800;
  }
  starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
  const starMat = new THREE.PointsMaterial({
    color: 0x00e5ff,
    size: 1.6,
    transparent: true,
    opacity: 0.55,
  });
  bgStars = new THREE.Points(starGeo, starMat);
  bgScene.add(bgStars);

  animateBackground();
}

/**
 * Animation loop for the 3D Hero Face Scanner.
 */
function animateHero() {
  requestAnimationFrame(animateHero);

  if (heroControls) {
    heroControls.update();
  }

  // Sweep laser up and down
  if (scanLaser) {
    if (isLaserGoingDown) {
      laserY -= 0.022;
      if (laserY <= -1.7) isLaserGoingDown = false;
    } else {
      laserY += 0.022;
      if (laserY >= 1.7) isLaserGoingDown = true;
    }
    scanLaser.position.y = laserY;
    scanLaser.rotation.z += 0.01;
  }

  // Rotate gimbal rings
  if (gimbalRing1) {
    gimbalRing1.rotation.x += 0.005;
    gimbalRing1.rotation.y += 0.008;
  }
  if (gimbalRing2) {
    gimbalRing2.rotation.y -= 0.006;
    gimbalRing2.rotation.z += 0.004;
  }

  // Slowly rotate face group
  if (faceGroup) {
    faceGroup.rotation.y += 0.003;
  }

  if (particleSystem) {
    particleSystem.rotation.y += 0.0008;
  }

  if (heroRenderer && heroScene && heroCamera) {
    heroRenderer.render(heroScene, heroCamera);
  }
}

/**
 * Animation loop for background starfield.
 */
function animateBackground() {
  requestAnimationFrame(animateBackground);

  if (bgStars) {
    bgStars.rotation.y += 0.0003;
    bgStars.rotation.x += 0.0001;
  }

  if (bgRenderer && bgScene && bgCamera) {
    bgRenderer.render(bgScene, bgCamera);
  }
}

/**
 * Handles window resizing.
 */
function onWindowResize() {
  // Hero viewport resize
  const container = document.getElementById('three-hero-viewport');
  if (container && heroCamera && heroRenderer) {
    const w = container.clientWidth;
    const h = container.clientHeight;
    heroCamera.aspect = w / h;
    heroCamera.updateProjectionMatrix();
    heroRenderer.setSize(w, h);
  }

  // Background starfield resize
  if (bgCamera && bgRenderer) {
    bgCamera.aspect = window.innerWidth / window.innerHeight;
    bgCamera.updateProjectionMatrix();
    bgRenderer.setSize(window.innerWidth, window.innerHeight);
  }
}
