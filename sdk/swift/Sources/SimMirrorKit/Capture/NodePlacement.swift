// SPDX-License-Identifier: Apache-2.0
#if DEBUG && targetEnvironment(simulator)
    import CoreGraphics

    /// Where in a window's nodes something found another way belongs: the node in the same place, or else the node
    /// it sits inside.
    enum NodePlacement {
        /// How much of their joined area two frames share: intersection over union.
        static func overlap(_ first: CGRect, _ second: CGRect) -> Double {
            let shared = first.intersection(second)
            guard !shared.isNull else { return 0 }
            let sharedArea = Double(shared.width * shared.height)
            let union = Double(first.width * first.height + second.width * second.height) - sharedArea
            return union > 0 ? sharedArea / union : 0
        }

        /// The path to the node that shares at least `threshold` of its area with `frame`: the deepest, then the
        /// closest one.
        static func match(_ frame: CGRect, in nodes: [Node], threshold: Double) -> [Int]? {
            var best: (path: [Int], overlap: Double)?
            func visit(_ nodes: [Node], _ path: [Int]) {
                for (offset, node) in nodes.enumerated() {
                    let here = path + [offset]
                    let shared = overlap(frame, node.frame.rect)
                    if shared >= threshold,
                        best.map({ here.count > $0.path.count || here.count == $0.path.count && shared > $0.overlap })
                            ?? true
                    {
                        best = (here, shared)
                    }
                    visit(node.children, here)
                }
            }
            visit(nodes, [])
            return best?.path
        }

        /// Changes the node at a path.
        static func update(_ nodes: inout [Node], at path: [Int], _ change: (inout Node) -> Void) {
            guard let first = path.first else { return }
            if path.count == 1 {
                change(&nodes[first])
            } else {
                update(&nodes[first].children, at: Array(path.dropFirst()), change)
            }
        }

        /// Adds a node inside the deepest node whose frame holds its middle, or beside the others when none does.
        static func insert(_ node: Node, into nodes: inout [Node]) {
            let middle = CGPoint(x: node.frame.rect.midX, y: node.frame.rect.midY)
            if let index = nodes.lastIndex(where: { $0.frame.rect.contains(middle) && $0.frame.rect != node.frame.rect }
            ) {
                insert(node, into: &nodes[index].children)
            } else {
                nodes.append(node)
            }
        }
    }
#endif
