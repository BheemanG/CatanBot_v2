import json
from collections import deque

# =====================================================================
# 1. THE FULL CATAN GRAPH
# Replace/expand this dictionary with your full 54-vertex coordinates.
# =====================================================================
CATAN_GRAPH = {
    0: [1, 8], 1: [0, 2], 2: [1, 3, 10], 3: [2, 4], 4: [3, 5, 12],
    5: [4, 6], 6: [5, 14], 7: [8, 17], 8: [0, 7, 9], 9: [8, 10, 19],
    10: [2, 9, 11], 11: [10, 12, 21], 12: [4, 11, 13], 13: [12, 14, 23],
    14: [6, 13, 15], 15: [14, 25],
    # ... Continue adding vertices 16 through 53 here
}

# =====================================================================
# 2. THE BFS DISTANCE CALCULATOR
# =====================================================================
def bfs_nodes_at_distance(graph, start_vertex, target_distance):
    """Finds all vertices exactly `target_distance` edges away."""
    if target_distance == 0:
        return [start_vertex]

    queue = deque([(start_vertex, 0)])
    visited = {start_vertex}
    results = []

    while queue:
        current_vertex, current_distance = queue.popleft()

        if current_distance == target_distance:
            results.append(current_vertex)
            continue 

        for neighbor in graph.get(current_vertex, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, current_distance + 1))

    return sorted(results)

# =====================================================================
# 3. GENERATION AND STORAGE
# =====================================================================
def main():
    max_distance = 11
    lookup_table = {}
    
    print("Generating Catan distance lookup table...")
    
    # Run BFS for every vertex on the board
    for vertex in CATAN_GRAPH.keys():
        lookup_table[vertex] = {}
        for distance in range(1, max_distance + 1):
            nodes = bfs_nodes_at_distance(CATAN_GRAPH, vertex, distance)
            
            # Only save the distance layer if nodes actually exist at that distance
            if nodes:
                lookup_table[vertex][distance] = nodes

    # Save data permanently to a JSON file
    output_filename = "catan_lookup.json"
    with open(output_filename, "w") as f:
        json.dump(lookup_table, f, indent=4)
        
    print(f"Success! Precomputed data saved to '{output_filename}'")

if __name__ == "__main__":
    main()