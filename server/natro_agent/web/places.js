/* The dashboard's Places page: his places on Google's map, saved over /dashboard/socket (places_page.py). A click on
   the map drops a pin; only the place being edited can be moved or resized, and nothing is saved until Save. With no
   map (no key, or Google refused it) the list, the search and the form still work. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const nonce = document.currentScript.nonce;
  const stacked = matchMedia("(max-width: 1180px)");  // as places.css lays it out
  const USUAL_RADIUS = 150, LEAST_RADIUS = 100, MOST_RADIUS = 5000;
  const START = { lat: 41.7151, lng: 44.8271 };  // Tbilisi, until he has places
  const NEAR_ZOOM = 15;
  const DELETE_SECONDS = 4;
  const AGAIN_SECONDS = 3;

  let places = [];
  let editing = null;  // the place in the form: {id (none: new), name, names, lat, lng, radius, address}
  let socket = null;
  let map = null, Circle = null, Label = null;
  let shapes = [], editingLabel = null;  // what's drawn on the map now, and the name of the place in the form
  let fitted = false;
  let deleting = null;  // the timer while Delete waits for a second click

  function h(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    }
    node.append(...children);
    return node;
  }

  // The socket

  function send(message) {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(message));
      return true;
    }
    return false;
  }

  function linked(on) {
    $("link-text").textContent = on ? "Connected" : "Connecting…";
    $("link").querySelector(".dot").className = on ? "dot" : "dot off";
    $("save").disabled = !on;
  }

  function connect() {
    socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/dashboard/socket`);
    socket.addEventListener("open", () => linked(true));
    socket.addEventListener("close", () => {
      linked(false);
      setTimeout(connect, AGAIN_SECONDS * 1000);
    });
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.type === "places") gotPlaces(message.places);
      else if (message.type === "place_saved") saved();
      else if (message.type === "place_found") showFound(message.text, message.found);
      else if (message.type === "place_error") gotError(message);
    });
  }

  function gotPlaces(all) {
    places = all;
    if (editing && editing.id && !places.some((place) => place.id === editing.id)) closeForm();  // deleted elsewhere
    if (editing) showHappens();
    showList();
    draw();
    fit();
  }

  function saved() {
    closeForm();
  }

  function gotError(message) {
    if (message.for === "place_search") {
      $("found").hidden = false;
      $("found").replaceChildren(h("p", { text: message.message }));
    } else {
      $("save").disabled = false;
      $("form-error").hidden = false;
      $("form-error").textContent = message.message;
    }
  }

  // The list

  function placeWords(place) {
    const others = place.names.length ? `Also ${place.names.join(", ")}. ` : "";
    const set = place.happens.length ? ` ${place.happens.length} set to happen.` : "";
    return `${others}${place.address || `${place.lat.toFixed(5)}, ${place.lng.toFixed(5)}`}. Within ${place.radius} m.${set}`;
  }

  // What's set to happen there, set by asking Natro.
  function showHappens() {
    const happens = places.find((place) => place.id === editing.id)?.happens;
    $("happens").hidden = !editing.id;
    if (!editing.id) return;
    $("happens").replaceChildren(...(happens?.length ? happens.map((each) => h("div", { class: "happens-row" },
      h("span", { text: `${each.on === "left" ? "When you leave" : "When you get there"}${each.every ? ", every time" : ""}: ${each.text}` }),
      h("button", { type: "button", "aria-label": `Stop: ${each.text}`, text: "×",
                    onclick: () => send({ type: "place_happening_delete", id: each.id }) }),
    )) : [h("p", { text: "Nothing set to happen here. Ask Natro: \"remind me to buy milk when I get here\"." })]));
  }

  function showList() {
    $("count").textContent = places.length || "";
    $("list").replaceChildren(...(places.length ? places.map((place) => h("button", {
      type: "button", class: "place-row", "aria-current": String(editing?.id === place.id),
      onclick: () => { edit(place); lookAt(place); },
    }, h("b", { text: place.name }), h("span", { text: placeWords(place) }))) : [
      h("p", { class: "empty", text: map ? "None yet. Click the map to drop a pin." : "None yet. Find an address to add one." }),
    ]));
  }

  // The form

  function edit(place) {
    editing = { ...place, names: [...place.names] };
    stopDeleting();
    $("form").hidden = false;
    $("form-title").textContent = place.id ? place.name : "New place";
    $("name").value = place.name;
    $("names").value = place.names.join(", ");
    $("radius").value = place.radius;
    $("delete").hidden = !place.id;
    $("form-error").hidden = true;
    $("save").disabled = socket?.readyState !== WebSocket.OPEN;
    showAddress();
    showHappens();
    showList();
    draw();
    if (stacked.matches) $("form").scrollIntoView({ block: "nearest" });  // it's under the map
    if (!place.id) $("name").focus({ preventScroll: true });
  }

  function newPlace(lat, lng, address = "") {
    edit({ name: editing && !editing.id ? editing.name : "", names: editing && !editing.id ? editing.names : [],
           lat, lng, radius: editing && !editing.id ? editing.radius : USUAL_RADIUS, address });
  }

  function closeForm() {
    editing = null;
    stopDeleting();
    $("form").hidden = true;
    showList();
    draw();
  }

  function showAddress() {
    const place = places.find((each) => each.id === editing.id);
    const moved = place && (place.lat !== editing.lat || place.lng !== editing.lng);
    $("address").textContent = moved ? "Moved: its address comes when it's saved."
      : editing.address || `${editing.lat.toFixed(5)}, ${editing.lng.toFixed(5)}: its address comes when it's saved.`;
  }

  function stopDeleting() {
    clearTimeout(deleting);
    deleting = null;
    $("delete").textContent = "Delete";
  }

  $("form").addEventListener("submit", (event) => {
    event.preventDefault();
    const name = $("name").value.trim();
    const radius = Number($("radius").value);
    const problem = !name ? "Give it a name." : !(radius >= LEAST_RADIUS && radius <= MOST_RADIUS)
      ? `How near is from ${LEAST_RADIUS} to ${MOST_RADIUS} m.` : "";
    $("form-error").hidden = !problem;
    $("form-error").textContent = problem;
    if (problem) return;
    const names = $("names").value.split(",").map((each) => each.trim()).filter(Boolean);
    if (send({ type: "place_save", id: editing.id, name, names, lat: editing.lat, lng: editing.lng, radius,
               address: editing.address })) {
      $("save").disabled = true;
    }
  });
  $("cancel").addEventListener("click", closeForm);
  $("delete").addEventListener("click", () => {
    if (deleting) {
      if (send({ type: "place_delete", id: editing.id })) closeForm();
      return;
    }
    $("delete").textContent = `Delete ${editing.name}?`;
    deleting = setTimeout(stopDeleting, DELETE_SECONDS * 1000);
  });
  $("name").addEventListener("input", () => {
    editing.name = $("name").value;
    if (editingLabel) editingLabel.node.textContent = editing.name.trim() || "New place";
  });
  $("radius").addEventListener("change", () => {
    const radius = Number($("radius").value);
    if (radius >= LEAST_RADIUS && radius <= MOST_RADIUS) {
      editing.radius = radius;
      draw();
    }
  });

  // The search

  $("search").addEventListener("keydown", (event) => {
    if (event.key === "Escape") $("found").hidden = true;
    if (event.key !== "Enter") return;
    event.preventDefault();
    const text = $("search").value.trim();
    if (text && send({ type: "place_search", text })) {
      $("found").hidden = false;
      $("found").replaceChildren(h("p", { text: "Looking…" }));
    }
  });

  function showFound(text, found) {
    $("found").hidden = false;
    $("found").replaceChildren(...(found.length ? found.map((each) => h("button", {
      type: "button", text: each.address,
      onclick: () => {
        $("found").hidden = true;
        newPlace(each.lat, each.lng, each.address);
        lookAt(each);
      },
    })) : [h("p", { text: `Google found nothing for ${text}.` })]));
  }

  // The map

  function mapNote(text) {
    $("map-note").hidden = false;
    $("map-note").textContent = text;
  }

  function lookAt(place) {
    if (!map) return;
    map.panTo({ lat: place.lat, lng: place.lng });
    if (map.getZoom() < NEAR_ZOOM) map.setZoom(NEAR_ZOOM);
  }

  function fit() {
    if (!map || fitted || !places.length) return;
    fitted = true;
    if (places.length === 1) {
      map.setCenter({ lat: places[0].lat, lng: places[0].lng });
      map.setZoom(NEAR_ZOOM);
      return;
    }
    const bounds = new google.maps.LatLngBounds();
    for (const place of places) bounds.extend({ lat: place.lat, lng: place.lng });
    map.fitBounds(bounds, 80);
  }

  function draw() {
    if (!map) return;
    for (const shape of shapes) shape.setMap(null);
    shapes = [];
    for (const place of places) {
      if (editing?.id === place.id) continue;
      const open = () => { edit(place); lookAt(place); };
      const circle = new Circle({ map, center: { lat: place.lat, lng: place.lng }, radius: place.radius, strokeColor: "#0a7a0d",
                                  strokeWeight: 1.5, strokeOpacity: 0.9, fillColor: "#0fb912", fillOpacity: 0.16 });
      circle.addListener("click", open);
      const label = new Label(place.name, circle.getCenter(), open);
      label.setMap(map);
      shapes.push(circle, label);
    }
    if (editing) drawEditing();
  }

  function drawEditing() {
    const center = { lat: editing.lat, lng: editing.lng };
    const circle = new Circle({ map, center, radius: editing.radius, editable: true, draggable: true, zIndex: 2,
                                strokeColor: "#0a7a0d", strokeWeight: 2, fillColor: "#0fb912", fillOpacity: 0.28 });
    const label = new Label(editing.name.trim() || "New place", circle.getCenter(), null);
    label.setMap(map);
    editingLabel = label;
    circle.addListener("center_changed", () => {
      const moved = circle.getCenter();
      editing.lat = moved.lat();
      editing.lng = moved.lng();
      editing.address = "";  // Google's, for where it was
      label.move(moved);
      showAddress();
    });
    circle.addListener("radius_changed", () => {
      const radius = Math.min(MOST_RADIUS, Math.max(LEAST_RADIUS, Math.round(circle.getRadius())));
      if (radius !== circle.getRadius()) circle.setRadius(radius);
      editing.radius = radius;
      $("radius").value = radius;
    });
    shapes.push(circle, label);
  }

  function defineLabel(OverlayView) {
    // A place's name on the map, at its middle.
    return class extends OverlayView {
      constructor(text, at, onclick) {
        super();
        this.at = at;
        this.node = h("div", { class: onclick ? "map-label" : "map-label editing", text });
        if (onclick) {
          this.node.addEventListener("click", onclick);
          OverlayView.preventMapHitsAndGesturesFrom(this.node);
        }
      }
      onAdd() {
        this.getPanes().overlayMouseTarget.append(this.node);
      }
      draw() {
        const point = this.getProjection()?.fromLatLngToDivPixel(this.at);
        if (point) {
          this.node.style.left = `${point.x}px`;
          this.node.style.top = `${point.y}px`;
        }
      }
      onRemove() {
        this.node.remove();
      }
      move(at) {
        this.at = at;
        this.draw();
      }
    };
  }

  window.natroMapReady = async () => {
    const library = await google.maps.importLibrary("maps");
    Circle = library.Circle;
    Label = defineLabel(library.OverlayView);
    map = new library.Map($("map"), {
      center: START, zoom: 12, colorScheme: "FOLLOW_SYSTEM", clickableIcons: false, gestureHandling: "greedy",
      streetViewControl: false, mapTypeControl: false, fullscreenControl: false,
    });
    map.addListener("click", (event) => {
      // A click beside the place he's editing doesn't take him away from it; a new pin goes where he clicks.
      if (editing && editing.id) return;
      $("found").hidden = true;
      newPlace(event.latLng.lat(), event.latLng.lng());
    });
    showList();
    draw();
    fit();
  };
  // Google calls this when it won't take the key.
  window.gm_authFailure = () => mapNote("Google refused the map's key. Its restriction has to allow this address: "
                                        + location.origin);

  const key = document.querySelector('meta[name="maps-key"]').content;
  if (key) {
    const script = h("script", {
      async: "", src: `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=weekly&loading=async&callback=natroMapReady`,
    });
    script.nonce = nonce;  // and from this to the scripts it adds
    document.head.append(script);
  } else {
    mapNote("No map yet: it needs its key, GOOGLE_MAPS_BROWSER_KEY. Places can still be found by address, and saved.");
  }
  showList();
  linked(false);
  connect();
})();
